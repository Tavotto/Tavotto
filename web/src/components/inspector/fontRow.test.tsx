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

const triggers = () => [...host.querySelectorAll<HTMLButtonElement>('[data-select-trigger]')]
const byLabel = (label: string) => triggers().find((t) => t.getAttribute('aria-label') === label)
const fontManifest = (el: ManifestElement, extra: Partial<Manifest> = {}) =>
  ({ ...manifestOf(MACHINE, undefined, [el]), font_latin_only_families: ['Arial', 'Avenir'], ...extra }) as Manifest

describe('属性页字体合成一行（英文 | 中文）', () => {
  it('有中文回退能力：只有一个「字体」标签、两个并排下拉各有可访问名称，没有 ⓘ', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const labels = [...host.querySelectorAll('[data-row-label]')].map((n) => n.textContent)
    expect(labels.filter((l) => l?.includes('字体'))).toEqual(['字体'])
    expect(triggers().map((t) => t.getAttribute('aria-label'))).toEqual(
      expect.arrayContaining(['英文字体', '中文字体']),
    )
    expect(byLabel('英文字体')!.parentElement!.className).toContain('flex-1')
    expect(byLabel('中文字体')!.parentElement!.className).toContain('w-[66px]')
    expect(byLabel('英文字体')!.closest('[data-row-grid]')).toBe(byLabel('中文字体')!.closest('[data-row-grid]'))
    expect(host.querySelector('[data-font-fallback-hint]')).toBeNull()
    expect(byLabel('中文字体')!.textContent).toContain('自动')
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
    expect(byLabel('中文字体')!.textContent).toBe('中宋体')
    expect(byLabel('中文字体')!.getAttribute('title')).toBe('宋体-简')
  })

  it('只有中文格有灰色前缀「中」，英文格没有（aria-hidden，可访问名称不变）；单格时没有前缀', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const prefixOf = (label: string) => byLabel(label)!.querySelector('[data-select-prefix]')
    expect(prefixOf('英文字体')).toBeNull()
    expect(prefixOf('中文字体')?.textContent).toBe('中')
    expect(prefixOf('中文字体')?.getAttribute('aria-hidden')).toBe('true')
    expect(prefixOf('中文字体')?.className).toContain('text-ink-3')
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
    expect(byLabel('中文字体')).toBeUndefined()
    expect(byLabel('英文字体')).toBeUndefined()
    expect(byLabel('字体')).toBeDefined()
  })

  it('截断悬停：英文格 title 是完整名，中文格 title 是所选字体名', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(byLabel('英文字体')!.getAttribute('title')).toBeTruthy()
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    expect(byLabel('中文字体')!.getAttribute('title')).toBe('思源黑体')
  })

  it('英文字体带中文字形：中文那格置灰，悬停说明沿用 cjkFallbackShadowed', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(byLabel('中文字体')!.disabled).toBe(false)
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    // 主字体此刻是 serif（不在 latin_only）→ 带中文字形 → 置灰
    expect(byLabel('中文字体')!.disabled).toBe(true)
    expect(byLabel('中文字体')!.getAttribute('title')).toBe('请改选不含中文字符的英文字体。')
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    expect(byLabel('中文字体')!.disabled).toBe(false)
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
    expect(byLabel('中文字体')!.textContent).toContain('多个值')
    expect(byLabel('英文字体')!.textContent).not.toContain('多个值')
  })

  it('改过：任一项改过就有恢复钮；点一下两项一起恢复，只加一条历史', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    expect(host.querySelector('[data-reset-prop]')).toBeNull()
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体')) // 只改了中文
    expect(host.querySelectorAll('[data-reset-prop]')).toHaveLength(1)
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    const before = useDocumentStore.getState().past.length
    await act(async () => host.querySelector<HTMLButtonElement>('[data-reset-prop]')!.click())
    expect(overrideOf(el.gid, 'fontfamily')).toBeUndefined()
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    await act(async () => { useDocumentStore.getState().undo() })
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: 'Arial', fallback: '思源黑体' })
    expect(host.querySelector('[data-reset-prop]')).not.toBeNull()
  })

  it('问题面板跳转：两个锚点各自指向自己那一格', async () => {
    const el = withFallback(titleEl)
    selected = [el]
    await mount(fontManifest(el))
    const en = host.querySelector('[data-prop="fontfamily"]')!
    const cjk = host.querySelector('[data-prop="fontfamily.fallback"]')!
    expect(en.querySelector('[aria-label="英文字体"]')).not.toBeNull()
    expect(cjk.querySelector('[aria-label="中文字体"]')).not.toBeNull()
    expect(cjk.querySelector('[aria-label="英文字体"]')).toBeNull()
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
