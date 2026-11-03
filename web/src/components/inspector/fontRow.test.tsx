/**
 * 图内文字的字体下拉并上**本机字体族**（2026-09-13 用户反馈：支持的字体太少）。
 *
 * 引擎按元素发的 `fontfamily.options` 只有首选项（通用族 + 装了的几个具名候选 +
 * 脚本自己那个）；本机的几百个族整份 manifest 只发一次（`font_families`）。
 * 并表只在 `withMachineFamilies` 一处，`useFigureTypography` 的 `fieldOf` 与写入
 * 前的校验拿的必须是同一份——否则下拉里选得到、写下去却被判成「不是选项」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { FIGURE_TEXT_SINGLE_PROPS, useFigureTypography, type TypographyAdapter } from './typographyAdapter'
import { TypographyControls } from './controls/TypographyControls'
import { propertyPathOf } from '@/lib/typography'
import { TooltipProvider } from '../ui/Tooltip'

/** 写入会触发一次渲染：像真引擎那样把同一份 manifest（含本机表）再发回来 */
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

const PREFERRED = ['serif', 'sans-serif', 'monospace', 'Arial']
/** 引擎排好序的本机表；Arial 与首选项重合，Zapfino / 思源黑体 只有本机表里有 */
const MACHINE = ['Arial', 'Avenir', 'DejaVu Sans', 'Zapfino', '思源黑体']

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题 “Map”',
  bbox: [0.4, 0.05, 0.2, 0.04],
  draggable: true,
  editable: [
    f('text', 'text', 'Map'),
    f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
    f('color', 'color', '#000000'),
    f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
    f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
    f('fontfamily', 'enum', 'serif', { options: PREFERRED }),
    f('ha', 'enum', 'center', { options: ['left', 'center', 'right'] }),
  ],
} as unknown as ManifestElement

/** 引擎读 name 表给出的中文名（`font_family_names`）：只有有中文名的才在表里 */

const manifestOf = (families?: string[], names?: Record<string, string>, elements: ManifestElement[] = [titleEl]): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [101.6, 76.2],
    elements,
    ...(families ? { font_families: families } : {}),
    ...(names ? { font_family_names: names } : {}),
  }) as unknown as Manifest

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

let root: Root
let host: HTMLDivElement
let adapter: TypographyAdapter

/** 选中的元素（默认标题；个别用例换成带 `options_unavailable` 的那一份） */
let selected: ManifestElement[] = [titleEl]

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  adapter = useFigureTypography(panel, selected, FIGURE_TEXT_SINGLE_PROPS)
  return (
    <TooltipProvider>
      <div data-inspector-gid={selected[0]?.gid}>
        <TypographyControls adapter={adapter} labelWidth="grid" />
      </div>
    </TooltipProvider>
  )
}

async function mount(manifest: Manifest) {
  resetGestureCoordinator()
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: '<svg/>', warnings: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_font_families')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  seedExactRender(panelOf(), manifest)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Harness />))
}

const overrideOf = (gid: string, prop: string) => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
  return p.overrides.find((o) => o.gid === gid && o.prop === prop)?.value
}

beforeEach(() => {
  localStorage.clear()
  useRenderStore.getState().clear()
})

afterEach(async () => {
  selected = [titleEl]
  await act(async () => root?.unmount())
  host?.remove()
  resetGestureCoordinator()
})


const withFallback = (el: ManifestElement, extra: Record<string, unknown> = {}) =>
  ({
    ...el,
    editable: el.editable.map((x) =>
      x.prop === 'fontfamily' ? { ...x, font_fallback_selection: true, ...extra } : x,
    ),
  }) as ManifestElement

/** 经 `data-prop` 锚点（`propertyPathOf` 给的路径）找那一格的下拉触发器；不认 aria-label，不取第一个匹配 */
const cell = (prop: 'fontFamily' | 'fontFallback') => {
  const anchors = host.querySelectorAll(`[data-prop="${propertyPathOf('figureText', prop)}"]`)
  if (anchors.length === 0) return undefined
  expect(anchors, `${prop} 的锚点必须唯一`).toHaveLength(1)
  const triggers = anchors[0].querySelectorAll<HTMLButtonElement>('[data-select-trigger]')
  expect(triggers, `${prop} 的锚点里恰有一个下拉`).toHaveLength(1)
  return triggers[0]
}
const resetChips = () => host.querySelectorAll<HTMLButtonElement>('[data-row-status] [data-reset-prop]')
/** Radix 的触发器认 pointerdown，`click()` 不开菜单 */
const openResetMenu = async () => {
  const chips = resetChips()
  expect(chips).toHaveLength(1)
  await act(async () => {
    chips[0].dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
  })
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}
const resetItem = (field: string) => document.querySelector<HTMLElement>(`[role="menuitem"][data-reset-field="${field}"]`)
const clickEl = async (el: Element | null | undefined) => {
  if (!el) throw new Error('没有这个按钮')
  await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
}
const fontManifest = (el: ManifestElement, extra: Partial<Manifest> = {}) =>
  ({ ...manifestOf(MACHINE, undefined, [el]), font_latin_only_families: ['Arial', 'Avenir'], ...extra }) as Manifest

describe('属性页字体合成一行（英文 | 中文）', () => {
  it('有中文回退能力：只有一个「字体」标签、两个并排下拉各有可访问名称，没有 ⓘ', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const labels = [...host.querySelectorAll('[data-row-label]')].map((n) => n.textContent)
    expect(labels.filter((l) => l?.includes('字体'))).toEqual(['字体'])
    expect(cell('fontFamily')).toBeDefined()
    expect(cell('fontFallback')).toBeDefined()
    expect(cell('fontFamily')).not.toBe(cell('fontFallback'))
    expect(cell('fontFamily')!.parentElement!.className).toContain('flex-1')
    expect(cell('fontFallback')!.parentElement!.className).toContain('w-[66px]')
    expect(cell('fontFamily')!.closest('[data-row-grid]')).toBe(cell('fontFallback')!.closest('[data-row-grid]'))
    expect(host.querySelector('[data-font-fallback-hint]')).toBeNull()
    expect(cell('fontFallback')!.textContent).toContain('自动')
  })

  it('中文格收起时只显示短名（完整名在列表与悬停里）', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount({ ...fontManifest(el), font_family_names: { 'Songti SC': '宋体-简' } } as Manifest)
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    useDocumentStore.getState().commit(literal('塞'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides = [{ gid: el.gid, prop: 'fontfamily', value: { primary: 'Arial', fallback: 'Songti SC' } }] as PanelObject['overrides']
    })
    await act(async () => root.render(<Harness />))
    expect(cell('fontFallback')!.textContent).toBe('中宋体')
    expect(cell('fontFallback')!.getAttribute('title')).toBe('宋体-简')
  })

  it('只有中文格有灰色前缀「中」，英文格没有（aria-hidden，可访问名称不变）；单格时没有前缀', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const prefixOf = (which: 'fontFamily' | 'fontFallback') => cell(which)!.querySelector('[data-select-prefix]')
    expect(prefixOf('fontFamily')).toBeNull()
    expect(prefixOf('fontFallback')?.textContent).toBe('中')
    expect(prefixOf('fontFallback')?.getAttribute('aria-hidden')).toBe('true')
    expect(prefixOf('fontFallback')?.className).toContain('text-ink-3')
    await act(async () => root.unmount())
    host.remove()
    useRenderStore.getState().clear()
    selected = [titleEl]
    await mount(manifestOf(MACHINE))
    expect(host.querySelector('[data-select-prefix]')).toBeNull()
  })

  it('没有中文回退能力：这一行只有一个英文下拉，沿用原来的「字体」标签', async () => {
    selected = [titleEl]
    await mount(manifestOf(MACHINE))
    expect(cell('fontFallback')).toBeUndefined()
    expect(cell('fontFamily')).toBeDefined()
  })

  it('截断悬停：英文格 title 是完整名，中文格 title 是所选字体名', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(cell('fontFamily')!.getAttribute('title')).toBeTruthy()
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    expect(cell('fontFallback')!.getAttribute('title')).toBe('思源黑体')
  })

  it('英文字体带中文字形：中文那格置灰，悬停说明沿用 cjkFallbackShadowed', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(cell('fontFallback')!.disabled).toBe(false)
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    // 主字体此刻是 serif（不在 latin_only）→ 带中文字形 → 置灰
    expect(cell('fontFallback')!.disabled).toBe(true)
    expect(cell('fontFallback')!.getAttribute('title')).toBe('请改选不含中文字符的英文字体。')
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    expect(cell('fontFallback')!.disabled).toBe(false)
  })

  it('混合值：多选且中文字体不一致，中文格显示「多个值」，英文格不受影响', async () => {
    const other = withFallback({ ...titleEl, gid: 'axes_0.xlabel' } as ManifestElement)
    const el = withFallback(titleEl)
    const manifest = { ...fontManifest(el), elements: [el, other] } as Manifest
    selected = [el]
    await mount(manifest)
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    selected = [el, other]
    await act(async () => root.render(<Harness />))
    expect(cell('fontFallback')!.textContent).toContain('多个值')
    expect(cell('fontFamily')!.textContent).not.toContain('多个值')
  })

  it('改过：只改一项时恢复钮只清那一项（各自可恢复，注册表规则）', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(resetChips()).toHaveLength(0)
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体')) // 只改了中文
    expect(resetChips()).toHaveLength(1)
    expect(host.querySelector('[data-reset-menu]')).toBeNull() // 一项：直接是 ResetChip，不是菜单
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    await act(async () => adapter.reset('fontFamily')) // 只恢复英文
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: null, fallback: '思源黑体' })
    await clickEl(resetChips()[0]) // 此刻只剩中文改过：钮就是中文的
    expect(overrideOf(el.gid, 'fontfamily')).toBeUndefined()
    expect(resetChips()).toHaveLength(0)
  })

  it('两项都改过：恢复英文后中文还在、恢复中文后英文还在；各一条历史；撤销各自还原；「两项都恢复」一条历史', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const both = { primary: 'Arial', fallback: '思源黑体' }
    const past = () => useDocumentStore.getState().past.length
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual(both)
    expect(host.querySelector('[data-reset-menu]')).not.toBeNull()

    // 适配器层：恢复英文，中文还在，一条历史，撤销还原
    let before = past()
    await act(async () => adapter.reset('fontFamily'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: null, fallback: '思源黑体' })
    expect(past()).toBe(before + 1)
    await act(async () => { useDocumentStore.getState().undo() })
    expect(overrideOf(el.gid, 'fontfamily')).toEqual(both)
    // 恢复中文，英文还在
    before = past()
    await act(async () => adapter.reset('fontFallback'))
    expect(overrideOf(el.gid, 'fontfamily')).toBe('Arial')
    expect(past()).toBe(before + 1)
    await act(async () => { useDocumentStore.getState().undo() })
    expect(overrideOf(el.gid, 'fontfamily')).toEqual(both)

    // 界面层：小菜单三项，各清各的
    await openResetMenu()
    expect([...document.querySelectorAll('[role="menuitem"]')].map((m) => m.getAttribute('data-reset-field'))).toEqual(['fontFamily', 'fontFallback', '*'])
    await clickEl(resetItem('fontFamily'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: null, fallback: '思源黑体' })
    await act(async () => { useDocumentStore.getState().undo() })
    await openResetMenu()
    await clickEl(resetItem('fontFallback'))
    expect(overrideOf(el.gid, 'fontfamily')).toBe('Arial')
    await act(async () => { useDocumentStore.getState().undo() })
    await openResetMenu()
    before = past()
    await clickEl(resetItem('*'))
    expect(overrideOf(el.gid, 'fontfamily')).toBeUndefined()
    expect(past()).toBe(before + 1)
    await act(async () => { useDocumentStore.getState().undo() })
    expect(overrideOf(el.gid, 'fontfamily')).toEqual(both)
  })

  it('问题面板跳转：两个锚点各自指向自己那一格', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const en = host.querySelector('[data-prop="fontfamily"]')!
    const cjk = host.querySelector('[data-prop="fontfamily.fallback"]')!
    expect(en.querySelector('[data-select-trigger]')).toBe(cell('fontFamily'))
    expect(cjk.querySelector('[data-select-trigger]')).toBe(cell('fontFallback'))
    expect(en.contains(cell('fontFallback')!)).toBe(false)
    expect(cjk.contains(cell('fontFamily')!)).toBe(false)
  })

  it('字体缺失提示：英文与中文两种缺失都显示在这一行下面', async () => {
    const el = withFallback(titleEl, { options: ['Gone Sans', ...PREFERRED], options_unavailable: ['Gone Sans'] })
    selected = [el]
    await mount(fontManifest(el))
    expect(host.textContent).not.toContain('没装这个字体')
    // 直接塞 override：中文写成一个本机没有的名字（写入口只认选项）
    useDocumentStore.getState().commit(literal('塞'), (d) => {
      const p = d.objects.find((o) => o.id === 'p1') as PanelObject
      p.overrides = [{ gid: el.gid, prop: 'fontfamily', value: { primary: 'Gone Sans', fallback: 'Ghost Han' } }] as PanelObject['overrides']
    })
    await act(async () => root.render(<Harness />))
    expect(host.textContent!.split('这台电脑没装这个字体').length - 1).toBe(2)
  })
})
