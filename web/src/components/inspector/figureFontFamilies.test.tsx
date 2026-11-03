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

import { applyLocale, literal } from '@/i18n'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { withMachineFamilies } from '@/lib/typography'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { fontFamilyOptionLabel } from './roles/registry'
import { fontFamilySelectModel } from './controls/fontFamilySelect'
import { FIGURE_TEXT_SINGLE_PROPS, useFigureTypography, type TypographyAdapter } from './typographyAdapter'

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
const NAMES = { 'Songti SC': '宋体-简', Avenir: 'Avenir' }

const manifestOf = (families?: string[], names?: Record<string, string>): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [101.6, 76.2],
    elements: [titleEl],
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
  return null
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

describe('withMachineFamilies（并表的唯一出处）', () => {
  it('首选项在前、本机的按序接在后面、去重', () => {
    const field = f('fontfamily', 'enum', 'serif', { options: PREFERRED })
    expect(withMachineFamilies(field, MACHINE)?.options).toEqual([
      ...PREFERRED,
      'Avenir',
      'DejaVu Sans',
      'Zapfino',
      '思源黑体',
    ])
  })

  it('别的字段、没有本机表、本机表没有新名字：原样返回同一个对象（memo 不白白失效）', () => {
    const weight = f('weight', 'enum', 'normal', { options: ['normal', 'bold'] })
    expect(withMachineFamilies(weight, MACHINE)).toBe(weight)
    const field = f('fontfamily', 'enum', 'serif', { options: PREFERRED })
    expect(withMachineFamilies(field, undefined)).toBe(field)
    expect(withMachineFamilies(field, [])).toBe(field)
    expect(withMachineFamilies(field, ['Arial'])).toBe(field)
    expect(withMachineFamilies(undefined, MACHINE)).toBeUndefined()
  })

  it('本机表里有的名字不算不可用：字段级 options_unavailable 标错了（刻度字体族曾按「不在首选项里」标）以本机表为准', () => {
    const field = f('fontfamily', 'enum', 'Avenir', {
      options: ['Avenir', ...PREFERRED],
      options_unavailable: ['Avenir', 'No Such Font'],
    })
    const merged = withMachineFamilies(field, MACHINE)!
    expect(merged.options_unavailable).toEqual(['No Such Font'])
    // 本机表没有新名字、也没有要撤的标记：原样返回同一个对象
    const clean = f('fontfamily', 'enum', 'Nope', { options: ['Nope', 'Arial'], options_unavailable: ['Nope'] })
    expect(withMachineFamilies(clean, ['Arial'])).toBe(clean)
  })
})

describe('useFigureTypography：属性页的「未安装」与本机表同一口径', () => {
  it('字段标了不可用、本机表里却有：属性页不再标它；真没装的照标', async () => {
    const el = {
      ...titleEl,
      editable: titleEl.editable.map((x) =>
        x.prop === 'fontfamily'
          ? f('fontfamily', 'enum', 'Avenir', { options: ['Avenir', ...PREFERRED], options_unavailable: ['Avenir'] })
          : x,
      ),
    } as ManifestElement
    selected = [el]
    await mount({ ...manifestOf(MACHINE), elements: [el] } as unknown as Manifest)
    expect(adapter.unavailableOptions('fontFamily')).toEqual([])
    await act(async () => root.unmount())
    host.remove()
    useRenderStore.getState().clear()
    await mount({ ...manifestOf(['Arial']), elements: [el] } as unknown as Manifest)
    expect(adapter.unavailableOptions('fontFamily')).toEqual(['Avenir'])
  })
})

describe('useFigureTypography：字体下拉与写入校验拿同一份表', () => {
  it('中文回退与主字体独立修改并可恢复自动，旧引擎不显示入口', async () => {
    const el = { ...titleEl, editable: titleEl.editable.map((x) =>
      x.prop === 'fontfamily' ? { ...x, font_fallback_selection: true } : x,
    ) } as ManifestElement
    selected = [el]
    await mount({ ...manifestOf(MACHINE), font_latin_only_families: ['Arial', 'Avenir'], elements: [el] } as Manifest)
    expect(adapter.valueOf('fontFallback')).toEqual({ kind: 'uniform', value: '' })
    await act(async () => adapter.writeOnce('fontFallback', '思源黑体'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: null, fallback: '思源黑体' })
    expect(adapter.fontFallbackShadowed).toBe(true)
    expect(adapter.fieldOf('fontFamily')?.options).toContain('Arial')
    expect(adapter.fieldOf('fontFamily')?.options).not.toContain('思源黑体')
    await act(async () => adapter.writeOnce('fontFamily', '思源黑体'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: null, fallback: '思源黑体' })
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    expect(overrideOf(el.gid, 'fontfamily')).toEqual({ primary: 'Arial', fallback: '思源黑体' })
    expect(adapter.fontFallbackShadowed).toBe(false)
    await act(async () => adapter.writeOnce('fontFallback', null))
    expect(overrideOf(el.gid, 'fontfamily')).toBe('Arial')
    await act(async () => adapter.reset('fontFamily'))
    expect(overrideOf(el.gid, 'fontfamily')).toBeUndefined()
  })

  it('下拉里能看到本机字体，选一个本机才有的字体真的写得下去', async () => {
    await mount(manifestOf(MACHINE))
    const field = adapter.fieldOf('fontFamily')!
    expect(field.options).toEqual([...PREFERRED, 'Avenir', 'DejaVu Sans', 'Zapfino', '思源黑体'])
    await act(async () => adapter.writeOnce('fontFamily', 'Zapfino'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBe('Zapfino')
    await act(async () => adapter.writeOnce('fontFamily', '思源黑体'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBe('思源黑体')
  })

  it('两张表都没有的名字仍然被挡在事务之外', async () => {
    await mount(manifestOf(MACHINE))
    await act(async () => adapter.writeOnce('fontFamily', 'No Such Font'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBeUndefined()
  })

  it('老引擎不发本机表：下拉照旧只有首选项，本机字体名写不进去', async () => {
    await mount(manifestOf())
    expect(adapter.fieldOf('fontFamily')!.options).toEqual(PREFERRED)
    await act(async () => adapter.writeOnce('fontFamily', 'Zapfino'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBeUndefined()
    await act(async () => adapter.writeOnce('fontFamily', 'Arial'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBe('Arial')
  })
})

describe('字体的中文显示名（manifest 顶层 font_family_names）', () => {
  afterEach(async () => {
    await applyLocale('zh-CN')
  })

  it('并表时把显示名挂到字段上；没给显示名就不挂，也不白白换对象', () => {
    const field = f('fontfamily', 'enum', 'serif', { options: PREFERRED })
    const merged = withMachineFamilies(field, [...MACHINE, 'Songti SC'], NAMES)
    expect(merged?.option_labels).toBe(NAMES)
    expect(merged?.options).toContain('Songti SC')
    // 只有显示名、没有新族：照样挂（首选项里的族也可能有中文名）
    expect(withMachineFamilies(field, ['Arial'], NAMES)?.option_labels).toBe(NAMES)
    expect(withMachineFamilies(field, ['Arial'], {})).toBe(field)
    // 已经挂的是同一张表：原样返回
    const again = withMachineFamilies(merged, [...MACHINE, 'Songti SC'], NAMES)
    expect(again).toBe(merged)
  })

  it('中文界面只显示中文名，英文界面只显示族名；没有中文名的、通用族照旧', async () => {
    const opts = ['Songti SC', 'Zapfino', 'Avenir', 'serif']
    expect(fontFamilyOptionLabel('Songti SC', NAMES, opts)).toBe('宋体-简')
    expect(fontFamilyOptionLabel('Songti SC', undefined, opts)).toBe('Songti SC')
    expect(fontFamilyOptionLabel('Zapfino', NAMES, opts)).toBe('Zapfino')
    // 显示名与族名相同就不重复一遍
    expect(fontFamilyOptionLabel('Avenir', NAMES, opts)).toBe('Avenir')
    // 通用族仍走翻译表（serif → 衬线之类），不因为有显示名表就原样漏出
    expect(fontFamilyOptionLabel('serif', NAMES, opts)).toBe(optionLabelOfSerif())
    await applyLocale('en-US')
    expect(fontFamilyOptionLabel('Songti SC', NAMES, opts)).toBe('Songti SC')
  })

  it('两个族中文名相同：只有它们补上族名，分得开', () => {
    const shared = {
      BiauKaiHK: '標楷體-港澳',
      'BiauKaiHK Regular': '標楷體-港澳',
      'Songti SC': '宋体-简',
    }
    const opts = Object.keys(shared)
    expect(fontFamilyOptionLabel('BiauKaiHK', shared, opts)).toBe('標楷體-港澳（BiauKaiHK）')
    expect(fontFamilyOptionLabel('BiauKaiHK Regular', shared, opts)).toBe(
      '標楷體-港澳（BiauKaiHK Regular）',
    )
    expect(fontFamilyOptionLabel('Songti SC', shared, opts)).toBe('宋体-简')
    // 显示名表是整份 manifest 的；只数这个下拉里的项——选项之外的同名族不让唯一看得见
    // 的那一项补族名（StylePanel 一行的选项只是表的子集）
    expect(fontFamilyOptionLabel('BiauKaiHK', shared, ['BiauKaiHK', 'Arial'])).toBe('標楷體-港澳')
  })

  it('有中文名的族撞上一个本身就叫这个名字的族：没有显示名的那个也算进撞名', () => {
    // 只有中文名的「宋体」按真名登记、表里没有它；Songti SC 的中文名也是「宋体」
    const labels = { 'Songti SC': '宋体', 'Kaiti SC': '楷体-简' }
    const opts = ['宋体', 'Songti SC', 'Kaiti SC', 'Arial']
    const shown = opts.map((o) => fontFamilyOptionLabel(o, labels, opts))
    expect(shown).toEqual(['宋体', '宋体（Songti SC）', '楷体-简', 'Arial'])
    expect(new Set(shown).size).toBe(opts.length)
    // 下拉里没有那个「宋体」族：不补
    expect(fontFamilyOptionLabel('Songti SC', labels, ['Songti SC', 'Arial'])).toBe('宋体')
  })

  it('下拉显示中文名，写进 override 的仍是族名', async () => {
    await mount(manifestOf([...MACHINE, 'Songti SC'], NAMES))
    const field = adapter.fieldOf('fontFamily')!
    expect(field.option_labels).toEqual(NAMES)
    await act(async () => adapter.writeOnce('fontFamily', 'Songti SC'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBe('Songti SC')
    // 显示名不是选项：写不进去
    await act(async () => adapter.writeOnce('fontFamily', '宋体-简'))
    expect(overrideOf('axes_0.title', 'fontfamily')).toBe('Songti SC')
  })
})

function optionLabelOfSerif(): string {
  return fontFamilyOptionLabel('serif', undefined, [])
}

describe('字体下拉模型 fontFamilySelectModel（只挑具体字体）', () => {
  const opts = ['serif', 'sans-serif', 'monospace', 'Arial', 'Times New Roman']
  const faces = { serif: 'Times New Roman', 'sans-serif': 'Arial', monospace: 'DejaVu Sans Mono' }
  const model = (value: string, over: Partial<Parameters<typeof fontFamilySelectModel>[0]> = {}) => {
    const onChange = vi.fn()
    return { m: fontFamilySelectModel({ value, options: opts, labels: undefined, faces, onChange, ...over }), onChange }
  }

  it('值是 serif：列表里没有通用族，选中的是画出来的那张脸（不重复），悬停说明来源', () => {
    const { m } = model('serif')
    expect(m.value).toBe('Times New Roman')
    expect(m.options.map((o) => o.value)).toEqual(['Arial', 'Times New Roman'])
    expect(m.options.some((o) => ['serif', 'sans-serif', 'monospace'].includes(o.value))).toBe(false)
    expect(m.options.filter((o) => o.value === 'Times New Roman')).toHaveLength(1)
    expect(m.title).toContain('serif')
  })

  it('画出来的脸不在选项表里（退到 DejaVu Serif）：补成一行才能显示为选中', () => {
    const { m } = model('serif', { faces: { ...faces, serif: 'DejaVu Serif' } })
    expect(m.value).toBe('DejaVu Serif')
    expect(m.options.map((o) => o.value)).toEqual(['DejaVu Serif', 'Arial', 'Times New Roman'])
  })

  it('选中正是那张脸的行：空操作，脚本保持 serif；选别的具体字体写显式名字', () => {
    const { m, onChange } = model('serif')
    m.onChange('Times New Roman')
    expect(onChange).not.toHaveBeenCalled()
    m.onChange('Arial')
    expect(onChange).toHaveBeenCalledExactlyOnceWith('Arial')
  })

  it('值是具体字体：不改值、不带说明；选项里同样没有通用族，选自己照常转发', () => {
    const { m, onChange } = model('Arial')
    expect(m.value).toBe('Arial')
    expect(m.title).toBeUndefined()
    expect(m.options.map((o) => o.value)).toEqual(['Arial', 'Times New Roman'])
    m.onChange('Times New Roman')
    expect(onChange).toHaveBeenCalledWith('Times New Roman')
  })

  it('老引擎没有 option_faces / 这个通用族解析不出：原样平铺，通用族留着当前值才有对应项', () => {
    const old = model('serif', { faces: undefined })
    expect(old.m.value).toBe('serif')
    expect(old.m.options.map((o) => o.value)).toEqual(opts)
    const partial = model('serif', { faces: { 'sans-serif': 'Arial' } })
    expect(partial.m.options.map((o) => o.value)).toEqual(['serif', 'monospace', 'Arial', 'Times New Roman'])
    partial.m.onChange('serif')
    expect(partial.onChange).toHaveBeenCalledWith('serif')
  })

  it('多选混合（值空串）：不替换值', () => {
    const { m } = model('')
    expect(m.value).toBe('')
    expect(m.title).toBeUndefined()
  })
})
