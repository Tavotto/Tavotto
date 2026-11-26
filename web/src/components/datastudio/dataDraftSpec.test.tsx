/**
 * 数据页右栏的「期刊」一行（草稿自己的规范绑定，`dataFigureStore.specBinding`）：
 * 一行在 X / Y / 分组之后；选刊 / 「不按期刊」只改草稿、不碰画布的 `doc.profile`、不进撤销栈，并按当前这份数据重出候选；
 * 候选响应带的 `figure_style` 在控件下面显示成一行真实数字；入口关着（`journalCatalogUi()` 假）时整行不出现。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataFigurePropose, type AiDataProposeResponse } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { loadCatalog } from '@/lib/journals'
import { configureDataFigureHost, useDataFigureStore } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { emptyProject } from '@/types/document'
import { DataPanel } from './DataPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false

const ui = vi.hoisted(() => ({ on: true }))
vi.mock('@/lib/journalUi', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/lib/journalUi')>()), journalCatalogUi: () => ui.on }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
const propose = vi.mocked(aiDataFigurePropose)

let root: Root
let host: HTMLDivElement
const st = () => useDataFigureStore.getState()
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

const cands = (style?: Record<string, number>): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: 'S', dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: [{ id: 'i1', title_code: 'line_band', why_code: 'line_band', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/i1', thumbnail_status: 'pending' }],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: ['g'] } },
    ...(style ? { figure_style: style } : {}),
  }) as AiDataProposeResponse

const ROLES = { x: 'x', y: 'y', yAll: ['y'], group: null, options: { x: ['x'], y: ['y'], group: ['g'] } }
const seed = (over: Record<string, unknown> = {}) =>
  useDataFigureStore.setState({ selected: { path: 'a.csv', sheet: null }, draftOpen: true, draftActive: true, phase: 'ready', session: 'S', roles: ROLES as never, specBinding: null, bindingCaptured: true, ...over })
const mount = async () => {
  await act(async () => {
    root.render(<TooltipProvider><DataPanel /></TooltipProvider>)
  })
}

beforeEach(async () => {
  ui.on = true
  localStorage.clear()
  propose.mockReset()
  setCurrentProjectId('pa')
  st().clear()
  configureDataFigureHost({
    openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {},
    resolveSpec: (b) => ({ resolved: b.journalRef?.id ?? b.id }),
  })
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_data_spec')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  st().clear()
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('一行「期刊」', () => {
  it('在 X / Y / 分组之后；标签是已有的「期刊」，没选时控件写「选择期刊」；用 data-data-role 锚点', async () => {
    seed()
    await mount()
    const order = [...document.querySelectorAll('[data-data-role]')].map((e) => e.getAttribute('data-data-role'))
    expect(order).toEqual(['journal', 'x', 'y', 'group'])
    const row = q('[data-data-role="journal"]')!
    expect(row.textContent).toContain('期刊')
    expect(row.querySelector('[data-draft-journal-picker]')!.textContent).toContain('选择期刊')
    // 草稿的那个不占设置里那个的 id / 锚点
    expect(q('#journal-picker')).toBeNull()
    expect(q('[data-journal-picker]')).toBeNull()
    expect(q('[data-data-spec]')).toBeNull() // 没有 figure_style 就没有数字那一行
  })

  it('触发器是同列下拉的那一副框（底色 / 高度），不是次按钮的灰胶囊', async () => {
    seed()
    await mount()
    const cls = q('[data-draft-journal-picker]')!.className.split(/\s+/)
    const selectCls = q('[data-data-role="x"] button')!.className.split(/\s+/)
    for (const c of ['bg-field', 'h-7', 'border', 'rounded-sm', 'px-2', 'w-full', 'hover:bg-field-hover']) {
      expect(cls).toContain(c)
      expect(selectCls).toContain(c)
    }
    expect(cls).not.toContain('bg-surface-hover')
    expect(cls).not.toContain('data-[state=open]:bg-surface-active')
  })

  it('读表阶段（还没有列角色）也在', async () => {
    seed({ phase: 'reading', roles: null })
    await mount()
    expect(q('[data-data-roles]')).toBeNull()
    expect(q('[data-data-role="journal"]')).not.toBeNull()
  })

  it('控件显示草稿绑定的刊名', async () => {
    seed({ specBinding: { id: 'journal-base-v1', journalRef: { id: 'fx-beta', catalogRev: 1, retrieved: '2026-11-01', label: 'Fixture Beta' } } })
    await mount()
    expect(q('[data-draft-journal-picker]')!.textContent).toContain('Fixture Beta')
  })

  it('入口关着：整行不出现', async () => {
    ui.on = false
    seed()
    await mount()
    expect(q('[data-data-role="journal"]')).toBeNull()
    expect(q('[data-draft-journal-picker]')).toBeNull()
    expect(q('[data-data-role="x"]')).not.toBeNull()
  })

  it('出图中禁用', async () => {
    seed({ drawingId: 'i1' })
    await mount()
    expect(q<HTMLButtonElement>('[data-draft-journal-picker]')!.disabled).toBe(true)
  })
})

describe('选刊只改草稿', () => {
  it('选一本：草稿绑定换成它、画布文档一个字节不动、没有撤销步；按当前数据带新 spec 重出候选', async () => {
    seed()
    propose.mockResolvedValue(cands())
    await mount()
    const before = { profile: useDocumentStore.getState().doc.profile, past: useDocumentStore.getState().past.length, doc: useDocumentStore.getState().doc }
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    await flush()
    expect(st().specBinding?.journalRef?.id).toBe('fx-beta')
    const now = useDocumentStore.getState()
    expect(now.doc).toBe(before.doc)
    expect(now.doc.profile).toBe(before.profile)
    expect(now.past.length).toBe(before.past)
    expect(propose).toHaveBeenCalledTimes(1)
    expect(propose.mock.calls[0]![0].spec).toEqual({ resolved: 'fx-beta' })
    expect(q('[data-draft-journal-picker]')!.textContent).toContain('Fixture Beta')
  })

  it('「不按期刊」：草稿退回默认规范（null），画布不动，重出候选不带 spec', async () => {
    seed({ specBinding: { id: 'journal-base-v1', journalRef: { id: 'fx-beta', catalogRev: 1, retrieved: '2026-11-01', label: 'Fixture Beta' } } })
    propose.mockResolvedValue(cands())
    await mount()
    const doc0 = useDocumentStore.getState().doc
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-none]'))
    await flush()
    expect(st().specBinding).toBeNull()
    expect(useDocumentStore.getState().doc).toBe(doc0)
    expect(propose).toHaveBeenCalledTimes(1)
    expect('spec' in propose.mock.calls[0]![0]).toBe(false)
  })

  it('选了期刊之后「不按期刊」退回选之前的那一份（草稿原来是课题组规范就回到它）', async () => {
    seed({ specBinding: { id: 'lab-spec', snapshot: { marker: 1 } } })
    propose.mockResolvedValue(cands())
    await mount()
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    await flush()
    expect(st().specBinding?.journalRef?.id).toBe('fx-beta')
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-none]'))
    await flush()
    expect(st().specBinding).toMatchObject({ id: 'lab-spec', snapshot: { marker: 1 } })
  })
})

describe('控件下面一行灰字（按 … 出图）', () => {
  const lineOf = () => q('[data-data-spec]')?.textContent
  const S = (extra: Record<string, number> = {}) => ({ width_single_mm: 89, width_double_mm: 183, font_pt: 6, ...extra })
  it('单栏、双栏、字号范围；不带多余的 0', async () => {
    seed({ figureStyle: S({ font_min_pt: 5, font_max_pt: 7.0, width_double_mm: 183.5 }) })
    await mount()
    expect(lineOf()).toBe('按单栏 89 mm、双栏 183.5 mm、5–7 pt 字号出图')
  })

  it('单栏 = 双栏写「图宽」；上下限相同只写一个', async () => {
    seed({ figureStyle: S({ width_double_mm: 89, font_min_pt: 6.5, font_max_pt: 6.5 }) })
    await mount()
    expect(lineOf()).toBe('按图宽 89 mm、6.5 pt 字号出图')
  })

  it('只有下限 / 只有上限 / 都没有（没有字号部分）', async () => {
    seed({ figureStyle: S({ font_min_pt: 5 }) })
    await mount()
    expect(lineOf()).toBe('按单栏 89 mm、双栏 183 mm、≥ 5 pt 字号出图')
    act(() => useDataFigureStore.setState({ figureStyle: S({ font_max_pt: 8 }) }))
    expect(lineOf()).toBe('按单栏 89 mm、双栏 183 mm、≤ 8 pt 字号出图')
    act(() => useDataFigureStore.setState({ figureStyle: S() }))
    expect(lineOf()).toBe('按单栏 89 mm、双栏 183 mm出图')
  })

  it('灰字在「期刊」那一行下面，与框内文字左对齐的缩进', async () => {
    seed({ figureStyle: S() })
    await mount()
    const row = q('[data-data-role="journal"]')!
    expect(row.lastElementChild).toBe(q('[data-data-spec]'))
    expect(q('[data-data-spec]')!.className).toContain('pl-[calc(56px+1rem)]')
    expect(row.className).toContain('mb-3')
  })

  it('换到别的刊之后新响应的数字替换旧的', async () => {
    seed({ figureStyle: S() })
    propose.mockResolvedValue(cands({ width_single_mm: 90, width_double_mm: 190, font_pt: 8, font_min_pt: 6, font_max_pt: 8 }))
    await mount()
    await click(q('[data-draft-journal-picker]'))
    await click(q('[data-journal-option="fx-beta"]'))
    await flush()
    expect(lineOf()).toBe('按单栏 90 mm、双栏 190 mm、6–8 pt 字号出图')
  })
})
