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
import { withMachineFamilies } from '@/lib/typography'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
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

const manifestOf = (families?: string[]): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [101.6, 76.2],
    elements: [titleEl],
    ...(families ? { font_families: families } : {}),
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

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  adapter = useFigureTypography(panel, [titleEl], FIGURE_TEXT_SINGLE_PROPS)
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
})

describe('useFigureTypography：字体下拉与写入校验拿同一份表', () => {
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
