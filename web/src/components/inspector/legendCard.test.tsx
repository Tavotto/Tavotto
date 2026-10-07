/**
 * 图例卡与图例项绑定（ADR 0034，Prompt 15）。
 *
 * 钉住的合同：
 *   1. 选中图例：首屏常驻位置 / 列数 / 示意线长 / 线与文字间距 / 行距 / 边框，
 *      列距只在多列时出现；字号与条目顺序由图例卡接管，通用列表里不再出第二套；
 *   2. 位置档位叫「最佳位置」（2026-09-15 之后它是同一组的第十格，可达名不变）；
 *   3. 条目列表按显示顺序、带跟随 / 自定义 / 未关联徽标；点文字选中那一项；
 *      上下移动写 `entry_order`（原始序号的排列）；显隐写那一项的 `visible`；
 *   4. 选中图例项：改示意线颜色 → 徽标立刻变「自定义」（不等渲染回来）；
 *      「恢复跟随」一次撤销撤掉全部示意线 override；
 *   5. 脚本原样是 custom 的项，「恢复跟随」写的是 `binding = follow_source`；
 *   6. 行里没有嵌套的可交互元素。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import {
  LEGEND_ENTRY_STYLE_PROPS,
  LEGEND_OUTSIDE_PRESETS,
  LEGEND_PLACEMENT_PROPS,
  LEGEND_PLACEMENT_SLOTS,
  entryBinding,
  legendDisplayOrder,
  legendEntryViews,
  legendPlacementPlan,
  placementPlanFrom,
  placementPropsOf,
  detachPlan,
  restoreFollowPlan,
  type LegendPlacementSlot,
} from '@/lib/legendModel'
import { ElementTree } from '@/components/left/ElementTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'
import { fieldVisible, presentFields } from './presentation/registry'
import { LEGEND_SPACING_PROPS } from './controls/LegendSpacingCard'

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

// 与 matplotlib 的 legend loc 全表同形：外侧预设（rightCenter = 'center left' …）的
// 落位判据现在会问「这个 loc 在不在 manifest 的选项里」，夹具只给五个的话「右侧中」
// 那一档是禁用的——点了什么都不写，用例量到的是夹具而不是产品
const LOCS = [
  'best',
  'upper right',
  'upper left',
  'lower left',
  'lower right',
  'right',
  'center left',
  'center right',
  'lower center',
  'upper center',
  'center',
]

/** 落位模型的源码原文——「清单是算出来的」那条判据读它 */
const LEGEND_MODEL_SRC = Object.values(
  import.meta.glob('/src/lib/legendModel.ts', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Record<string, string>,
)[0] as string

/** 与 engine/manifest.py `_legend_fields` 同形 */
const legendFields = (ncol = 1, anchor: unknown = null): EditableField[] => [
  f('loc', 'enum', 'best', { options: LOCS }),
  f('loc_anchor', 'pair', anchor, { min: -1, max: 2, step: 0.01 }),
  f('fontsize', 'number', 8, { min: 3, max: 24, step: 0.5, unit: 'pt' }),
  f('frameon', 'bool', true),
  f('visible', 'bool', true),
  f('title', 'text', '', { group: '样式' }),
  f('title_fontsize', 'number', 8, { min: 3, max: 24, step: 0.5, unit: 'pt', group: '样式' }),
  f('facecolor', 'color', '#ffffff', { group: '样式' }),
  f('framealpha', 'number', 0.8, { min: 0, max: 1, step: 0.05, group: '样式' }),
  f('edgecolor', 'color', '#cccccc', { group: '样式' }),
  f('entry_order', 'order', [0, 1, 2], { options: ['sin', 'cos', 'proxy'], group: '布局' }),
  f('ncol', 'number', ncol, { min: 1, max: 6, step: 1, group: '布局' }),
  f('borderpad', 'number', 0.4, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('labelspacing', 'number', 0.5, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('handlelength', 'number', 2, { min: 0, max: 5, step: 0.1, group: '布局' }),
  f('handletextpad', 'number', 0.8, { min: 0, max: 3, step: 0.1, group: '布局' }),
  f('columnspacing', 'number', 2, { min: 0, max: 6, step: 0.1, group: '布局' }),
  f('frame_linewidth', 'number', 0.8, { min: 0, max: 4, step: 0.1, unit: 'pt', group: '样式' }),
  f('frame_rounded', 'bool', true, { group: '样式' }),
]

/** 与 engine/manifest.py `_text_fields`（去 visible）+ `_legend_entry_fields` 同形 */
const entryFields = (
  text: string,
  over: { binding?: string; color?: string; withBinding?: boolean } = {},
): EditableField[] => [
  f('text', 'text', text),
  f('fontsize', 'number', 8, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
  f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif'] }),
  f('ha', 'enum', 'left', { options: ['left', 'center', 'right'], group: '排版' }),
  ...(over.withBinding === false
    ? []
    : [
        f('binding', 'enum', over.binding ?? 'follow_source', {
          options: ['follow_source', 'custom'],
          group: '图例项',
        }),
      ]),
  f('handle_color', 'color', over.color ?? '#ff0000', { group: '图例项' }),
  f('handle_linestyle', 'enum', '-', { options: ['-', '--', ':', '-.'], group: '图例项' }),
  f('handle_linewidth', 'number', 1.5, { min: 0.1, max: 8, step: 0.1, unit: 'pt', group: '图例项' }),
  f('handle_marker', 'enum', 'None', { options: ['None', 'o', 's'], group: '图例项' }),
  f('handle_markersize', 'number', 6, { min: 0, max: 20, step: 0.5, unit: 'pt', group: '图例项' }),
  f('visible', 'bool', true),
]

const legendEl: ManifestElement = {
  gid: 'axes_0.legend',
  role: 'legend',
  label: '图例',
  bbox: [0.6, 0.1, 0.3, 0.3],
  draggable: true,
  anchor: [0.6, 0.4],
  drag_prop: 'loc_frac',
  editable: legendFields(),
}

const entry = (
  j: number,
  text: string,
  info: ManifestElement['legend_entry'],
  over: Parameters<typeof entryFields>[1] = {},
): ManifestElement => ({
  gid: `axes_0.legend.texts_${j}`,
  role: 'legend_text',
  label: `图例项 “${text}”`,
  bbox: [0.65, 0.12 + j * 0.05, 0.2, 0.04],
  draggable: false,
  editable: entryFields(text, over),
  legend_entry: info,
})

const sinEntry = entry(0, 'sin', {
  index: 0,
  source_gid: 'axes_0.lines_0',
  binding_default: 'follow_source',
})
// 脚本自己改过示意线的项：源找得到，脚本原样是 custom
const cosEntry = entry(
  1,
  'cos',
  { index: 1, source_gid: 'axes_0.lines_1', binding_default: 'custom' },
  { binding: 'custom', color: '#0000ff' },
)
// 代理 artist：没有源，没有 binding 字段
const proxyEntry = entry(2, 'proxy', { index: 2 }, { withBinding: false, color: '#000000' })

const lineEl = (j: number, name: string): ManifestElement => ({
  gid: `axes_0.lines_${j}`,
  role: 'line',
  label: `曲线 “${name}”`,
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  editable: [f('color', 'color', '#ff0000'), f('linewidth', 'number', 1.5)],
})

const manifest: Manifest = {
  rev: 1,
  size_mm: [101.6, 76.2],
  elements: [lineEl(0, 'sin'), lineEl(1, 'cos'), legendEl, sinEntry, cosEntry, proxyEntry],
} as unknown as Manifest

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

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overridesOf = (gid: string) => livePanel().overrides.filter((o) => o.gid === gid)
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
const byAria = (name: string) => buttons().find((b) => b.getAttribute('aria-label') === name)
const byText = (text: string) => buttons().find((b) => b.textContent?.trim().split(', ')[0] === text)
/** 通用列表里某个字段的输入框（行锚点 `data-prop` 是定位服务的落点） */
const propInput = (prop: string, type?: string) =>
  Array.from(host.querySelectorAll(`[data-prop="${prop}"] input`)).find(
    (i) => !type || i.getAttribute('type') === type,
  ) as HTMLInputElement | undefined
/** 图例项那一行的主按钮（roving focus 的落点）与行尾菜单（⇧F10 打开同一份清单） */
const entryMain = (gid: string) =>
  host.querySelector<HTMLButtonElement>(`[data-legend-entry="${gid}"] [data-legend-entry-main]`)!
const key = async (el: Element, k: string, init: KeyboardEventInit = {}) => {
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
  })
}
const entryMenuItem = async (gid: string, sel: string) => {
  await key(host.querySelector(`[data-legend-entry="${gid}"]`)!, 'F10', { shiftKey: true })
  return document.querySelector(sel) ?? undefined
}
const labels = () =>
  Array.from(host.querySelectorAll('[data-prop]')).map((n) => n.getAttribute('data-prop'))
const click = async (el: Element | undefined) => {
  if (!el) throw new Error('没有这个按钮')
  await act(async () => {
    // 真浏览器里点按钮会先把焦点从正在编辑的输入框上挪走（blur 收掉那一轮
    // 文字手势）；jsdom 的 dispatchEvent 不动焦点，这里补上
    const active = document.activeElement
    if (active instanceof HTMLElement && active !== el) active.blur()
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
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
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_legend_card')
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

describe('legendModel', () => {
  it('显示顺序：override 优先，越界 / 重复忽略，缺漏按原序补尾', () => {
    expect(legendDisplayOrder(panelOf(), legendEl, 3)).toEqual([0, 1, 2])
    const p = panelOf([{ gid: legendEl.gid, prop: 'entry_order', value: [2, 9, 2, 0] }])
    expect(legendDisplayOrder(p, legendEl, 3)).toEqual([2, 0, 1])
  })

  it('每一项的绑定：handle_* override → custom；binding override；脚本原样；无源 → null', () => {
    expect(entryBinding(panelOf(), sinEntry)).toBe('follow_source')
    expect(entryBinding(panelOf(), cosEntry)).toBe('custom')
    expect(entryBinding(panelOf(), proxyEntry)).toBeNull()
    const styled = panelOf([{ gid: sinEntry.gid, prop: 'handle_color', value: '#123456' }])
    expect(entryBinding(styled, sinEntry)).toBe('custom')
    const told = panelOf([{ gid: cosEntry.gid, prop: 'binding', value: 'follow_source' }])
    expect(entryBinding(told, cosEntry)).toBe('follow_source')
    // handle_* 在时 binding override 说了不算——与引擎同一条规则
    const both = panelOf([
      { gid: cosEntry.gid, prop: 'binding', value: 'follow_source' },
      { gid: cosEntry.gid, prop: 'handle_linewidth', value: 3 },
    ])
    expect(entryBinding(both, cosEntry)).toBe('custom')
  })

  it('条目视图按显示顺序，带文字 / 绑定 / 显隐 / 源元素', () => {
    const p = panelOf([
      { gid: legendEl.gid, prop: 'entry_order', value: [2, 0, 1] },
      { gid: sinEntry.gid, prop: 'text', value: 'SIN' },
      { gid: cosEntry.gid, prop: 'visible', value: false },
    ])
    const views = legendEntryViews(p, manifest, legendEl)
    expect(views.map((v) => v.info.index)).toEqual([2, 0, 1])
    expect(views.map((v) => v.text)).toEqual(['proxy', 'SIN', 'cos'])
    expect(views.map((v) => v.binding)).toEqual([null, 'follow_source', 'custom'])
    expect(views.map((v) => v.hidden)).toEqual([false, false, true])
    expect(views[1].source?.gid).toBe('axes_0.lines_0')
  })

  it('恢复跟随的计划：脚本原样 custom 的项写 binding=follow_source，否则连 binding 一起删', () => {
    expect(restoreFollowPlan(sinEntry)).toEqual({
      remove: [
        { gid: sinEntry.gid, prop: 'handle_color' },
        { gid: sinEntry.gid, prop: 'handle_linestyle' },
        { gid: sinEntry.gid, prop: 'handle_linewidth' },
        { gid: sinEntry.gid, prop: 'handle_marker' },
        { gid: sinEntry.gid, prop: 'handle_markersize' },
        { gid: sinEntry.gid, prop: 'binding' },
      ],
      set: [],
    })
    expect(restoreFollowPlan(cosEntry).set).toEqual([
      { gid: cosEntry.gid, prop: 'binding', value: 'follow_source' },
    ])
  })

  it('断开的计划：binding=custom 连同此刻的五条示意线样式一起写进文档（#414）', () => {
    expect(detachPlan(sinEntry)).toEqual([
      { gid: sinEntry.gid, prop: 'binding', value: 'custom' },
      { gid: sinEntry.gid, prop: 'handle_color', value: '#ff0000' },
      { gid: sinEntry.gid, prop: 'handle_linestyle', value: '-' },
      { gid: sinEntry.gid, prop: 'handle_linewidth', value: 1.5 },
      { gid: sinEntry.gid, prop: 'handle_marker', value: 'None' },
      { gid: sinEntry.gid, prop: 'handle_markersize', value: 6 },
    ])
  })

  it('断开的计划只写 manifest 真的发了的样式字段：柱的示意线只有颜色', () => {
    const bar: ManifestElement = {
      ...sinEntry,
      gid: 'axes_0.legend.texts_4',
      editable: sinEntry.editable.filter(
        (f) => !f.prop.startsWith('handle_') || f.prop === 'handle_color',
      ),
    }
    expect(detachPlan(bar)).toEqual([
      { gid: bar.gid, prop: 'binding', value: 'custom' },
      { gid: bar.gid, prop: 'handle_color', value: '#ff0000' },
    ])
    // 断开写下的六条，恢复跟随全部删掉：两条计划互为逆
    expect(restoreFollowPlan(sinEntry).remove.map((o) => o.prop).sort()).toEqual(
      detachPlan(sinEntry).map((o) => o.prop).sort(),
    )
  })
})

/* -------------------------------- 首屏分桶 -------------------------------- */

describe('图例的首屏', () => {
  const buckets = (ncol: number) =>
    presentFields('legend', legendFields(ncol), {
      isOverridden: () => false,
      read: (prop) => legendFields(ncol).find((x) => x.prop === prop)?.value,
    })

  it('高频项常驻：位置 / 列数 / 边框四条（间距归排版详情，审计 T17）', () => {
    const primary = buckets(1).primary.map((p) => p.field.prop)
    expect(primary).toEqual([
      'loc',
      'loc_anchor',
      'ncol',
      'frameon',
      'frame_linewidth',
      'frame_rounded',
      'edgecolor',
      'facecolor',
    ])
    expect(buckets(1).more.map((p) => p.field.prop)).not.toContain('ncol')
    // 五条间距在通用列表里一条都不出现——它们由排版详情卡承接，
    // 同一属性不出两套控件（`LEGEND_SPACING_PROPS` 在分桶之前就被让出来了）
    const all = [...buckets(2).primary, ...buckets(2).more, ...buckets(2).advanced]
    const spacing = LEGEND_SPACING_PROPS as readonly string[]
    expect(all.map((p) => p.field.prop).filter((x) => spacing.includes(x))).toEqual([
      // 分桶函数本身不裁能力：这里喂的是**没被让出来**的原始字段表，
      // 五条都还在，只是不在 primary。真正的让出发生在 ElementInspector
      // （见下面「排版详情」一组的 DOM 断言）
      'borderpad',
      'labelspacing',
      'handlelength',
      'handletextpad',
      'columnspacing',
    ])
  })

  it('列距只在多列时出现（判据只有 fieldVisible 一条，卡与通用列表共用）', () => {
    const read = (ncol: number) => (prop: string) =>
      legendFields(ncol).find((x) => x.prop === prop)?.value
    const vis = (ncol: number, over = false) =>
      fieldVisible('legend', 'columnspacing', {
        isOverridden: () => over,
        read: read(ncol),
      })
    expect(vis(1)).toBe(false)
    expect(vis(2)).toBe(true)
    // 改过的必须能看到，哪怕此刻只有一列
    expect(vis(1, true)).toBe(true)
  })

  it('图例项的首屏：链接中只有文字 + 链接行，断开后才有示意线样式（审计 T18）', () => {
    const bucketsOf = (binding: string) => {
      const fields = entryFields('sin', { binding })
      return presentFields('legend_text', fields, {
        isOverridden: () => false,
        read: (prop) => fields.find((x) => x.prop === prop)?.value,
      })
    }
    const linked = bucketsOf('follow_source')
    const all = (b: ReturnType<typeof bucketsOf>) =>
      [...b.primary, ...b.more, ...b.advanced].map((p) => p.field.prop)
    expect(linked.primary.map((p) => p.field.prop)).toContain('binding')
    // 链接中：示意线的五条一条都不在**任何**桶里（收起来，不是挪进「更多」）
    for (const prop of LEGEND_ENTRY_STYLE_PROPS) expect(all(linked)).not.toContain(prop)

    const custom = bucketsOf('custom')
    const primary = custom.primary.map((p) => p.field.prop)
    expect(primary).toContain('handle_linestyle')
    // 标记大小仍要有标记（两条前提是与的关系，不是互相取代）
    expect(all(custom)).not.toContain('handle_markersize')
    expect(custom.primary.find((p) => p.field.prop === 'binding')?.control).toBe('legend-binding')
    expect(custom.primary.find((p) => p.field.prop === 'handle_linestyle')?.control).toBe('line-style')
    expect(custom.primary.find((p) => p.field.prop === 'handle_marker')?.control).toBe('marker')
  })

  it('改过的示意线样式照常显示，哪怕此刻是链接中', () => {
    const fields = entryFields('sin', { binding: 'follow_source' })
    const b = presentFields('legend_text', fields, {
      isOverridden: (prop) => prop === 'handle_color',
      read: (prop) => fields.find((x) => x.prop === prop)?.value,
    })
    expect(b.primary.map((p) => p.field.prop)).toContain('handle_color')
    expect(b.primary.map((p) => p.field.prop)).not.toContain('handle_linewidth')
  })
})

/* -------------------------------- 图例页 ---------------------------------- */

describe('选中图例', () => {
  it('位置档位的名字是「最佳位置」，不是无上下文的「自动」', async () => {
    await mount(['axes_0.legend'])
    // 2026-09-15 打磨把它做成第十格：格子里是塞得下的短写，**名字**仍在可达名上
    // （ADR 0034）。只断言可见文字的话，改可达名这条变异照样绿
    const best = buttons().find((b) => b.getAttribute('aria-label') === '最佳位置')
    expect(best).toBeDefined()
    expect(best!.textContent?.trim()).toBe('自动')
  })

  it('字号与条目顺序由图例卡接管，通用列表不再出第二套', async () => {
    await mount(['axes_0.legend'])
    // 「更多」展开之后才量得到：没接管的话那两条会落在折叠区里
    await click(byText('更多'))
    // 图例卡的 Typography 行有字号（锚点 data-prop=fontsize，来自 propertyPathOf）；
    // 通用列表里没有以 fontsize / entry_order 为锚点的第二行——锚点两处同名，
    // 数「一共几个」才量得到重复
    expect(host.querySelectorAll('[data-prop="fontsize"]').length).toBe(1)
    expect(labels().filter((p) => p === 'entry_order')).toHaveLength(0)
    // 条目列表：三项按显示顺序。**默认态（跟随）不挂徽标**（打磨 L10）——
    // 两项都是跟随时每行挂一枚「跟随」没有信息量；非默认的两种照旧说话
    const list = host.querySelector('ul[aria-label="图例项列表"]')!
    const rows = Array.from(list.querySelectorAll('li'))
    expect(rows.map((r) => r.textContent)).toEqual(['sin', 'cos自定义', 'proxy未关联'])
  })

  it('行里没有嵌套的可交互元素', async () => {
    await mount(['axes_0.legend'])
    const nested = host.querySelectorAll('button button, button input, a button')
    expect(nested.length).toBe(0)
  })

  // ------------------------------------------------------------------
  // 外侧锚点（ADR 0034 的 2026-09-07 修订）
  // ------------------------------------------------------------------
  it('锚点由位置控件的外侧带承接，通用列表里不出第二套裸 x/y', async () => {
    await mount(['axes_0.legend'])
    await click(byText('更多'))
    expect(labels().filter((p) => p === 'loc_anchor')).toHaveLength(0)
    expect(byAria('右侧上')).toBeDefined()
  })

  it('点外侧预设：loc 与锚点落进同一次修改（一条历史、一次渲染）', async () => {
    await mount(['axes_0.legend'])
    const before = useDocumentStore.getState().past.length
    await click(byAria('右侧上'))
    expect(overrideOf('axes_0.legend', 'loc')).toBe('upper left')
    expect(overrideOf('axes_0.legend', 'loc_anchor')).toEqual([1.02, 1])
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
  })

  it('点外侧预设会把拖动留下的 loc_frac 一并删掉——不然点了没反应', async () => {
    useDocumentStore.getState().commit(literal('先拖一下'), (d) => {
      const panel = d.objects.find((o) => o.id === 'p1') as PanelObject
      panel.overrides.push({ gid: 'axes_0.legend', prop: 'loc_frac', value: [0.2, 0.3] })
    })
    await mount(['axes_0.legend'])
    await click(byAria('右侧上'))
    expect(overrideOf('axes_0.legend', 'loc_frac')).toBeUndefined()
    expect(overrideOf('axes_0.legend', 'loc_anchor')).toEqual([1.02, 1])
  })

  it('此刻没有锚点时点九宫格不写 loc_anchor——不留一条没有作用的 override', async () => {
    await mount(['axes_0.legend'])
    await click(byAria('左上'))
    expect(overrideOf('axes_0.legend', 'loc')).toBe('upper left')
    expect(overridesOf('axes_0.legend').map((o) => o.prop)).not.toContain('loc_anchor')
  })

  it('此刻在外侧时点九宫格写 loc_anchor = null（那是一个取值，不是删掉它）', async () => {
    await mount(['axes_0.legend'])
    await click(byAria('右侧中'))
    await click(byAria('左上'))
    expect(overrideOf('axes_0.legend', 'loc')).toBe('upper left')
    expect(overrideOf('axes_0.legend', 'loc_anchor')).toBeNull()
    expect(overridesOf('axes_0.legend').map((o) => o.prop)).toContain('loc_anchor')
  })

  it('重置位置：loc 与 loc_anchor 一起回到脚本原值（图例回到图内）', async () => {
    await mount(['axes_0.legend'])
    await click(byAria('右侧上'))
    expect(overrideOf('axes_0.legend', 'loc')).toBe('upper left')
    expect(overrideOf('axes_0.legend', 'loc_anchor')).toEqual([1.02, 1])

    // 位置那一行的恢复按钮：`loc_anchor` 被这个控件承接了，通用列表里没有
    // 第二个入口能清它——只清 `loc` 的话锚框还在，图例仍然在图外
    await click(byAria('恢复位置'))
    expect(overrideOf('axes_0.legend', 'loc')).toBeUndefined()
    expect(overridesOf('axes_0.legend').map((o) => o.prop)).not.toContain('loc_anchor')
  })

  it('重置位置只动落位那一组，别的 override 一条不碰', async () => {
    useDocumentStore.getState().commit(literal('先改点别的'), (d) => {
      const panel = d.objects.find((o) => o.id === 'p1') as PanelObject
      panel.overrides.push({ gid: 'axes_0.legend', prop: 'fontsize', value: 12 })
      panel.overrides.push({ gid: 'axes_0.legend', prop: 'ncol', value: 2 })
      panel.overrides.push({ gid: 'axes_0.lines_0', prop: 'color', value: '#00ff00' })
    })
    await mount(['axes_0.legend'])
    await click(byAria('右侧上'))
    await click(byAria('恢复位置'))
    expect(overridesOf('axes_0.legend').map((o) => o.prop).sort()).toEqual(['fontsize', 'ncol'])
    expect(overrideOf('axes_0.lines_0', 'color')).toBe('#00ff00')
  })

  it('重置位置把拖动留下的 loc_frac 也清掉——控件写过它，就该清它', async () => {
    useDocumentStore.getState().commit(literal('先拖一下'), (d) => {
      const panel = d.objects.find((o) => o.id === 'p1') as PanelObject
      panel.overrides.push({ gid: 'axes_0.legend', prop: 'loc_frac', value: [0.2, 0.3] })
    })
    await mount(['axes_0.legend'])
    // 只拖过、没点过预设：位置那行照样算「已修改」，恢复按钮就在那儿
    await click(byAria('恢复位置'))
    expect(overridesOf('axes_0.legend')).toHaveLength(0)
  })

  it('一次重置 = 一条历史（不是三条）', async () => {
    await mount(['axes_0.legend'])
    await click(byAria('右侧上'))
    const before = useDocumentStore.getState().past.length
    await click(byAria('恢复位置'))
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
  })

  it('清单只有一份：位置控件写过的 prop 全在 LEGEND_PLACEMENT_PROPS 里', () => {
    // 结构判据。绕开槽位表直接 `set.push` 一条新 prop 时在这里红——那是
    // 「加了第四条落位 prop 却没进那张表」唯一还能溜进来的路。
    const panel = panelOf()
    const touched = new Set<string>()
    for (const next of [
      { loc: 'upper left', anchor: [1.02, 1] as [number, number] },
      { loc: 'upper left', anchor: null },
      ...LEGEND_OUTSIDE_PRESETS.map((preset) => ({ loc: preset.loc, anchor: preset.anchor })),
    ]) {
      const plan = legendPlacementPlan(panel, [legendEl], next)
      for (const r of plan.remove) touched.add(r.prop)
      for (const w of plan.set) touched.add(w.prop)
    }
    expect(touched.size).toBeGreaterThan(1)
    expect([...touched].filter((p) => !LEGEND_PLACEMENT_PROPS.includes(p))).toEqual([])
  })

  it('往槽位表加一条 prop：写入面与重置面同时变大（不是回来手改白名单）', () => {
    // 这条钉的是**推导关系本身**，不是今天那三条 prop 的取值。手写两份清单
    // 时，下次加落位 prop 的人不会自动想起还有个重置清单——重置漏掉它而且
    // 不会红，白名单式的判据只挡得住「已知那几条丢了」，挡不住「新增了第二类」。
    const extra: LegendPlacementSlot = { prop: 'loc_pad', plan: () => ({ value: 1 }) }
    const slots = [...LEGEND_PLACEMENT_SLOTS, extra]

    // 生产那份重置清单就是从这张表算出来的（不是另抄的一份字面量）
    expect(placementPropsOf(LEGEND_PLACEMENT_SLOTS)).toEqual([...LEGEND_PLACEMENT_PROPS])
    // 表长一条 → 清单跟着长一条
    expect(placementPropsOf(slots)).toContain('loc_pad')
    expect(placementPropsOf(slots)).toHaveLength(LEGEND_PLACEMENT_PROPS.length + 1)
    // 而且新槽位真的会被写出来——证明两侧读的确实是同一张表，不是各走各的
    const plan = placementPlanFrom(slots, panelOf(), [legendEl], {
      loc: 'upper left',
      anchor: [1.02, 1],
    })
    const touched = [...plan.remove, ...plan.set].map((t) => t.prop)
    expect(touched).toContain('loc_pad')
    expect(touched.filter((p) => !placementPropsOf(slots).includes(p))).toEqual([])
  })

  it('重置清单是**算出来的**，不是一份碰巧相等的字面量', () => {
    // 上一条用例只看得见取值：把 `placementPropsOf(...)` 换成一份今天恰好相等
    // 的手写数组，它照样绿——而那正是「退化回白名单」的样子。差别只在构造上，
    // 判据也只能落在构造上（读源码走 `?raw`，与 `ui/nativeSelect.test.ts` 同
    // 一手法：src 归 tsconfig.app.json 管，那儿不该有 node 的 types）。
    const DEFINED_BY_DERIVATION = /export const LEGEND_PLACEMENT_PROPS = placementPropsOf\(/
    expect(
      LEGEND_MODEL_SRC,
      '重置清单必须从 LEGEND_PLACEMENT_SLOTS 推导；手写第二份的话，下次加落位 '
        + 'prop 时重置会漏掉它而且不会红',
    ).toMatch(DEFINED_BY_DERIVATION)
    // 自检：判据认得出退化成字面量的写法（不是空门禁）
    expect(
      DEFINED_BY_DERIVATION.test(
        "export const LEGEND_PLACEMENT_PROPS = ['loc', 'loc_anchor', 'loc_frac']",
      ),
    ).toBe(false)
  })

  it('重置的覆盖面由清单说了算：里面的每一条都被清掉', async () => {
    // **遍历 `LEGEND_PLACEMENT_PROPS` 造 override**，不是手写三条。以后槽位表
    // 长一条，这条用例自动多造一条、也自动多要求清掉一条——覆盖面跟着变。
    const seed: Record<string, unknown> = {
      loc: 'upper left',
      loc_anchor: [1.02, 1],
      loc_frac: [0.2, 0.3],
    }
    useDocumentStore.getState().commit(literal('每条落位 prop 各造一条'), (d) => {
      const panel = d.objects.find((o) => o.id === 'p1') as PanelObject
      for (const prop of LEGEND_PLACEMENT_PROPS) {
        panel.overrides.push({ gid: 'axes_0.legend', prop, value: seed[prop] ?? 1 })
      }
      // 对照组：不属于这个控件的那条必须活下来
      panel.overrides.push({ gid: 'axes_0.legend', prop: 'fontsize', value: 12 })
    })
    await mount(['axes_0.legend'])
    await click(byAria('恢复位置'))
    expect(overridesOf('axes_0.legend').map((o) => o.prop)).toEqual(['fontsize'])
  })

  it('点文字选中那一项', async () => {
    await mount(['axes_0.legend'])
    await click(byAria('选中图例项 “cos”'))
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.legend.texts_1'])
  })

  it('焦点在项上时整张列表仍只有一个 Tab 停靠点（⋯ 不进 Tab 顺序）；⇧F10 / ContextMenu 键照样开菜单（Codex #829）', async () => {
    await mount(['axes_0.legend'])
    const gid = 'axes_0.legend.texts_1'
    const main = entryMain(gid)
    await act(async () => main.focus())
    expect(document.activeElement).toBe(main)
    // jsdom 不走 Tab：Tab 序列 = 列表里 tabIndex ≥ 0 的元素，必须只剩这一个主按钮，Tab 才会直接离开列表
    const list = host.querySelector('ul[aria-label="图例项列表"]')!
    const stops = Array.from(list.querySelectorAll<HTMLElement>('button, [tabindex]')).filter((el) => el.tabIndex >= 0)
    expect(stops).toEqual([main])
    const trigger = host.querySelector<HTMLButtonElement>(`[data-legend-entry-menu="${gid}"]`)!
    expect(trigger.tabIndex).toBe(-1)
    // 键盘入口：从获得焦点的主按钮发出（真实的冒泡路径），菜单从 ⋯ 垂下
    for (const init of [{ key: 'F10', shiftKey: true }, { key: 'ContextMenu' }]) {
      await key(main, init.key, init)
      expect(trigger.getAttribute('data-state')).toBe('open')
      expect(document.querySelector('[data-legend-toggle-hidden]')).not.toBeNull()
      await key(document.activeElement ?? document.body, 'Escape')
      expect(trigger.getAttribute('data-state')).toBe('closed')
    }
  })

  it('下移写 entry_order（原始序号的排列），一条历史', async () => {
    await mount(['axes_0.legend'])
    const before = useDocumentStore.getState().past.length
    // ⌥↓ = 下移（与 ⋯ 菜单里的「下移」同一个动作）
    await key(entryMain('axes_0.legend.texts_0'), 'ArrowDown', { altKey: true })
    expect(overrideOf('axes_0.legend', 'entry_order')).toEqual([1, 0, 2])
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    // 列表立刻按新顺序排（不等渲染回来）
    const list = host.querySelector('ul[aria-label="图例项列表"]')!
    expect(Array.from(list.querySelectorAll('li')).map((r) => r.textContent?.slice(0, 3))).toEqual([
      'cos',
      'sin',
      'pro',
    ])
  })

  /** 拖动柄按下（jsdom 里行高都是 0，任何松手位置都落到最后一行） */
  const pressDragHandle = async (gid: string) => {
    const handle = host.querySelector(`[data-legend-entry="${gid}"] [data-legend-drag]`)!
    await act(async () => {
      handle.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, clientY: 0 }))
    })
  }
  const releasePointer = async () => {
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerup', { clientY: 999 }))
    })
  }

  it('拖动柄松手写 entry_order，一条历史（下一条的对照）', async () => {
    await mount(['axes_0.legend'])
    const before = useDocumentStore.getState().past.length
    await pressDragHandle('axes_0.legend.texts_0')
    await releasePointer()
    expect(overrideOf('axes_0.legend', 'entry_order')).toEqual([1, 2, 0])
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
  })

  it('拖到一半检查器卸载（如松手前 Esc 退出元素编辑）：迟到的松手不重排、不进历史（Codex #829）', async () => {
    await mount(['axes_0.legend'])
    const before = useDocumentStore.getState().past.length
    await pressDragHandle('axes_0.legend.texts_0')
    await act(async () => {
      root.unmount()
    })
    await releasePointer()
    expect(overrideOf('axes_0.legend', 'entry_order')).toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(before)
  })

  it('已经重排过再移动：写的仍是原始序号的排列，不是显示位置', async () => {
    useDocumentStore.getState().commit(literal('先重排'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides.push({ gid: 'axes_0.legend', prop: 'entry_order', value: [2, 0, 1] })
    })
    await mount(['axes_0.legend'])
    await click(await entryMenuItem('axes_0.legend.texts_2', '[data-legend-move="down"]'))
    expect(overrideOf('axes_0.legend', 'entry_order')).toEqual([0, 2, 1])
  })

  it('隐藏写那一项的 visible=false；再点一次恢复', async () => {
    await mount(['axes_0.legend'])
    await click(await entryMenuItem('axes_0.legend.texts_0', '[data-legend-toggle-hidden]'))
    expect(overrideOf('axes_0.legend.texts_0', 'visible')).toBe(false)
    await click(await entryMenuItem('axes_0.legend.texts_0', '[data-legend-toggle-hidden]'))
    expect(overrideOf('axes_0.legend.texts_0', 'visible')).toBeUndefined()
  })
})

/* -------------------------------- 图例项页 -------------------------------- */

describe('选中图例项', () => {
  it('链接中的项：一行写清链接到谁，动作是一个链条开关（审计 T18）', async () => {
    await mount(['axes_0.legend.texts_0'])
    const state = host.querySelector('[data-binding]')
    expect(state?.getAttribute('data-binding')).toBe('follow_source')
    expect(state?.textContent).toBe('链接到：曲线 “sin”')
    const toggle = byAria('断开链接')
    expect(toggle).toBeDefined()
    expect(toggle?.getAttribute('aria-pressed')).toBe('true')
    // 关系仍然看得见：来源入口在（行尾的图标钮，名字带对象名；审计 B50 之后
    // 不再单独一行把对象名再念一遍）
    expect(byAria('查看源对象：曲线 “sin”')).toBeDefined()
    // 说明**不常驻**：原理在开关的悬停提示里（Radix 的气泡只在打开时才进 DOM）
    expect(host.textContent).not.toContain('示意线由图中那个对象派生')
  })

  it('链接中不摆示意线样式；断开后出现，恢复链接后又收起', async () => {
    await mount(['axes_0.legend.texts_0'])
    await click(byText('更多'))
    expect(propInput('handle_color', 'color')).toBeUndefined()
    expect(propInput('handle_linewidth')).toBeUndefined()

    await click(byAria('断开链接'))
    expect(host.querySelector('[data-binding]')?.getAttribute('data-binding')).toBe('custom')
    expect(host.querySelector('[data-binding]')?.textContent).toBe('已断开 · 来源：曲线 “sin”')
    expect(propInput('handle_color', 'color')).toBeDefined()
    // **断开之后关系仍然看得见**：来源入口留着。藏起来的话，改这一项就像是
    // 在改那条曲线本身——那正是这一条的验收（审计 T18）
    expect(byAria('查看源对象：曲线 “sin”')).toBeDefined()

    await click(byAria('恢复链接'))
    expect(host.querySelector('[data-binding]')?.getAttribute('data-binding')).toBe('follow_source')
    expect(propInput('handle_color', 'color')).toBeUndefined()
  })

  it('改示意线颜色 → 立刻是「自定义」，不等渲染回来', async () => {
    await mount(['axes_0.legend.texts_0'])
    // 先断开才有这个控件（审计 T18）；断开写的是 binding override
    await click(byAria('断开链接'))
    const color = propInput('handle_color', 'color')
    expect(color).toBeDefined()
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(color!, '#123456')
      color!.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(overrideOf('axes_0.legend.texts_0', 'handle_color')).toBe('#123456')
    expect(host.querySelector('[data-binding]')?.getAttribute('data-binding')).toBe('custom')
    // 判据是「任一 handle_* override 在即 custom」，**不是**「binding override 说了算」：
    // 把 binding override 拿掉（老文档 / 别处清过一次）状态仍然是自定义
    useDocumentStore.getState().commit(literal('去掉 binding override'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides = p.overrides.filter((o) => o.prop !== 'binding')
    })
    await act(async () => {})
    expect(host.querySelector('[data-binding]')?.getAttribute('data-binding')).toBe('custom')
  })

  it('断开一次写下 binding=custom 与此刻的五条样式（#414）；恢复链接一次撤销撤掉全部', async () => {
    await mount(['axes_0.legend.texts_0'])
    const start = useDocumentStore.getState().past.length
    await click(byAria('断开链接'))
    expect(overrideOf('axes_0.legend.texts_0', 'binding')).toBe('custom')
    // 此刻的样子按 manifest 的当前值定格进文档：重开后才是同一条示意线
    expect(overrideOf('axes_0.legend.texts_0', 'handle_color')).toBe('#ff0000')
    expect(overrideOf('axes_0.legend.texts_0', 'handle_linewidth')).toBe(1.5)
    expect(overridesOf('axes_0.legend.texts_0').map((o) => o.prop).sort()).toEqual([
      'binding',
      'handle_color',
      'handle_linestyle',
      'handle_linewidth',
      'handle_marker',
      'handle_markersize',
    ])
    expect(useDocumentStore.getState().past.length).toBe(start + 1)
    const before = useDocumentStore.getState().past.length
    await click(byAria('恢复链接'))
    expect(overridesOf('axes_0.legend.texts_0')).toEqual([])
    const past = useDocumentStore.getState().past
    expect(past.length, JSON.stringify(past.slice(before).map((h) => h.label))).toBe(before + 1)
    useDocumentStore.getState().undo()
    expect(overridesOf('axes_0.legend.texts_0').map((o) => o.prop).sort()).toEqual([
      'binding',
      'handle_color',
      'handle_linestyle',
      'handle_linewidth',
      'handle_marker',
      'handle_markersize',
    ])
  })

  it('脚本原样是 custom 的项：恢复链接写 binding=follow_source', async () => {
    await mount(['axes_0.legend.texts_1'])
    expect(host.querySelector('[data-binding]')?.getAttribute('data-binding')).toBe('custom')
    await click(byAria('恢复链接'))
    expect(overrideOf('axes_0.legend.texts_1', 'binding')).toBe('follow_source')
  })

  it('没有源的项：没有绑定行，示意线样式照常可编辑', async () => {
    await mount(['axes_0.legend.texts_2'])
    expect(host.querySelector('[data-binding]')).toBeNull()
    expect(propInput('handle_linewidth')).toBeDefined()
  })
})

/**
 * Codex #829 P2：元组 / 路径标记（`(5, 1, 0)`、`$...$`、Path）的取值字符串不是认得的名字，
 * 色样曾因此一笔不画。没被改过时照引擎的 `marker_current` 几何画（与标记选择器同一份）。
 */
describe('图例色样照引擎几何画标记', () => {
  it('handle_marker 是元组字符串时，色样里画的是 marker_current 那条路径', async () => {
    const tupleEntry: ManifestElement = {
      ...sinEntry,
      editable: sinEntry.editable.map((fld) =>
        fld.prop === 'handle_marker'
          ? {
              ...fld,
              value: '(5, 1, 0)',
              marker_current: {
                kind: 'path',
                vertices: [
                  [0, 0.5],
                  [-0.48, 0.15],
                  [-0.29, -0.4],
                  [0.29, -0.4],
                  [0.48, 0.15],
                  [0, 0],
                ],
                codes: [1, 2, 2, 2, 2, 79],
              },
            }
          : fld,
      ),
    }
    const m = {
      ...manifest,
      elements: manifest.elements.map((e) => (e.gid === sinEntry.gid ? tupleEntry : e)),
    } as Manifest
    useRenderStore.getState().patch(renderKeyOf(panelOf()), { manifest: m })
    await mount(['axes_0.legend'])
    const swatch = host.querySelector(`[data-legend-swatch="${sinEntry.gid}"]`)
    expect(swatch, '图例项那一行要有色样').not.toBeNull()
    const path = swatch!.querySelector('path')
    expect(path, '名字画不出的标记要照引擎几何画').not.toBeNull()
    expect(path!.getAttribute('d')).toMatch(/^M/)
  })
})

/**
 * Codex #829（仓库主人复现）：拖动会话属于**一个**图例。焦点留在元素树里（拖动柄的 pointerdown
 * 吞掉默认动作、不抢焦点），拖到一半按 ↓ 换到另一个图例——检查器里的图例卡被复用给新图例，
 * 迟到的松手绝不能拿旧闭包给已经不显示的那个图例写 `entry_order`、加一条历史。
 * 用真实的 `ElementTree` 键盘路径换选中，不直接改选区。
 */
describe('拖到一半在元素树里换图例', () => {
  const figLegend = (i: number): ManifestElement => ({
    ...legendEl,
    gid: `fig.legend_${i}`,
    label: `图例 ${i + 1}`,
  })
  const figEntry = (i: number, j: number, text: string): ManifestElement => ({
    ...entry(j, text, { index: j }, { withBinding: false }),
    gid: `fig.legend_${i}.texts_${j}`,
  })
  const A = 'fig.legend_0'
  const B = 'fig.legend_1'
  const twoLegends = {
    rev: 1,
    size_mm: [101.6, 76.2],
    elements: [
      { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], draggable: false, editable: [] },
      figLegend(0),
      figEntry(0, 0, 'a0'),
      figEntry(0, 1, 'a1'),
      figEntry(0, 2, 'a2'),
      figLegend(1),
      figEntry(1, 0, 'b0'),
      figEntry(1, 1, 'b1'),
      figEntry(1, 2, 'b2'),
    ],
  } as unknown as Manifest

  function TreeAndInspector() {
    const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
    return (
      <TooltipProvider>
        <ElementTree />
        <ElementInspector panel={panel} />
      </TooltipProvider>
    )
  }

  const treeRow = (gid: string) => host.querySelector<HTMLElement>(`[data-el="${CSS.escape(gid)}"]`)!
  const shownLegend = () => host.querySelector('[data-legend-entry]')?.getAttribute('data-legend-entry')
  const press = async (gid: string) => {
    const handle = host.querySelector(`[data-legend-entry="${gid}"] [data-legend-drag]`)!
    await act(async () => {
      handle.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, clientY: 0 }))
    })
  }
  /** jsdom 里行高都是 0：任何松手位置都落到最后一行——第 0 项挪到末尾 = [1, 2, 0] */
  const release = async () => {
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerup', { clientY: 999 }))
    })
  }

  beforeEach(async () => {
    seedExactRender(livePanel(), twoLegends)
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: [A] })
    useSelectionStore.getState().set(['p1'])
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root.render(<TreeAndInspector />)
    })
    await act(async () => {})
    useDocumentStore.setState({ past: [], future: [] })
  })

  it('焦点在树里 A 行，按住 A 的拖动柄，↓ 选中 B，再松手：A 不重排、不进历史', async () => {
    // 树里 A、B 是相邻两行（图例的项默认收起），↓ 正好从 A 走到 B
    await act(async () => treeRow(A).focus())
    expect(document.activeElement).toBe(treeRow(A))
    expect(shownLegend()).toBe(`${A}.texts_0`)

    await press(`${A}.texts_0`)
    expect(document.activeElement, '拖动柄不抢焦点：键盘仍在树上').toBe(treeRow(A))

    await act(async () => {
      treeRow(A).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    })
    await act(async () => {})
    expect(document.activeElement).toBe(treeRow(B))
    expect(useUiStore.getState().selectedGids).toEqual([B])
    expect(shownLegend(), '右侧已经换成 B').toBe(`${B}.texts_0`)

    await release()
    expect(overrideOf(A, 'entry_order'), '已经不显示的 A 不得被重排').toBeUndefined()
    expect(overrideOf(B, 'entry_order'), '也不得把旧的那次拖动记到 B 头上').toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(0)
  })

  it('对照：换到 B 之后在 B 上正常拖，照常写 B 的 entry_order、一条历史', async () => {
    await act(async () => treeRow(A).focus())
    await act(async () => {
      treeRow(A).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    })
    await act(async () => {})
    expect(shownLegend()).toBe(`${B}.texts_0`)

    await press(`${B}.texts_0`)
    await release()
    expect(overrideOf(B, 'entry_order')).toEqual([1, 2, 0])
    expect(overrideOf(A, 'entry_order')).toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(1)
  })
})
