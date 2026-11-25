/** 误差线后续：`bar_err` 的名字与说明、多个 y 的依据句与右栏「误差线」一行（各列一致 / 不同）、与「目的」原因句的配合、合画草稿。 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataFigurePropose, type AiDataProposeResponse } from '@/lib/api'
import { candidatesFromResponse } from '@/lib/dataFigureAdapter'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { CandidateStage } from './CandidateStage'
import { DataPanel } from './DataPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
const propose = vi.mocked(aiDataFigurePropose)

let root: Root
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(s: string) => document.querySelector<T>(s)
const qa = <T extends Element = HTMLElement>(s: string) => [...document.querySelectorAll<T>(s)]
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const mount = async (ui: React.ReactNode) => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{ui}</TooltipProvider>))
}
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useDataFigureStore.getState().clear()
})
const item = (id: string, code: string, extra: Record<string, unknown> = {}) => ({ id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...extra })
const facts = () => ({ rows: 12, drawn: 12, groups: 4, n_min: 3, n_max: 3, x_values: 4 })
const cols = (kinds: [string, string], stated = [true, true]) => [
  { y: 'a_mean', column: 'a_sd', kind: kinds[0], stated: stated[0] },
  { y: 'b_mean', column: 'b_sd', kind: kinds[1], stated: stated[1] },
]

describe('bar_err 的名字与说明', () => {
  const show = (extra: Record<string, unknown> = {}) => {
    const items = candidatesFromResponse([item('a', 'bar_err', { facts: facts(), error: 'sd', error_source: 'column', error_column: 'SD 列', ...extra })], 'pa')
    return mount(<CandidateStage items={items} thumbs={{ a: 'ready' }} drawingId={null} previewKey={null} purposeCode="compare_groups" onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
  }
  it('标题「柱 + 误差线」、说明「表里已经是各组的均值，用柱高表示。」；依据写「每行一根柱」', async () => {
    await show()
    expect(q('[data-candidate-title]')!.textContent).toBe('柱 + 误差线')
    expect(q('[data-candidate-why]')!.textContent).toBe('表里已经是各组的均值，用柱高表示。')
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按表里的 12 行绘制，每行一根柱；误差线取自「SD 列」列。')
  })
  it('没有 why_not（后端不给）：不多出一行，主图不让位', async () => {
    await show()
    expect(q('[data-candidate-whynot]')).toBeNull()
    act(() => q<HTMLImageElement>('[data-candidate-image] img')!.dispatchEvent(new Event('load')))
    expect(q<HTMLImageElement>('[data-candidate-image] img')!.style.maxHeight).toContain('100cqh - 250px')
  })
  it('用户选的目的它不回答时后端才给 why_not：照常显示那一句', async () => {
    await show({ why_not: { code: 'answers_other_purpose', guidance: [], tier: 'user' } })
    expect(q('[data-candidate-whynot]')!.textContent).toBe('你选的是「比较各组」，这张回答的是另一个问题。')
  })
})

describe('多个 y 的依据句', () => {
  it('误差线取自「a_sd」「b_sd」列（各自带「」、直接相连）', async () => {
    const items = candidatesFromResponse([item('a', 'line_err', { facts: facts(), error: 'sd', error_source: 'column', error_columns: cols(['sd', 'sd']) })], 'pa')
    await mount(<CandidateStage items={items} thumbs={{ a: 'ready' }} drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按表里的 12 行绘制，每条线每行一个点；误差线取自「a_sd」「b_sd」列。')
  })
})

describe('右栏「误差线」：多个 y', () => {
  const roles = { x: 'x', y: 'y', yAll: ['y'], group: null, stack: false, yExtra: 0, options: { x: ['x'], y: ['y'], group: [] } }
  const seed = (extra: Record<string, unknown>, over: Record<string, unknown> = {}) =>
    useDataFigureStore.setState({
      selected: { path: 'a.csv', sheet: null }, draftOpen: true, draftActive: true, phase: 'ready', session: 'S1', roles: roles as never,
      candidates: candidatesFromResponse([item('c0', 'line_err', { error_source: 'column', ...extra })], 'pa'), thumbs: { c0: 'ready' },
      errorConfirmed: false, designAsked: false, answers: {}, bindingCaptured: true, ...over,
    })
  const choose = async (value: string) => {
    await act(async () => {
      q('[data-data-role="error"] button')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    const opt = qa('[role=option]').find((o) => o.textContent === value)
    expect(opt, `没有选项 ${value}`).toBeTruthy()
    await act(async () => {
      opt!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
      opt!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
  }
  beforeEach(() => {
    propose.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
  })

  it('各列一致 + 都由列名说明：和单 y 一样，放在列角色下面、不进「需要你确认」', async () => {
    seed({ error: 'sd', error_columns: cols(['sd', 'sd']) })
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-data-roles] [data-data-role="error"]')!.textContent).toContain('标准差 (SD)')
  })

  it('各列一致但不是都说明了：按没说明处理（进「需要你确认」）', async () => {
    seed({ error: 'sd', error_columns: cols(['sd', 'sd'], [true, false]) })
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')!.textContent).toBe('需要你确认')
    expect(q('[data-data-confirm] [data-data-role="error"]')).not.toBeNull()
  })

  it('一致但是 unknown：「需要你确认」，当前值「还不确定」', async () => {
    seed({ error: 'unknown', error_columns: cols(['unknown', 'unknown'], [false, false]) })
    await mount(<DataPanel />)
    expect(q('[data-data-confirm] [data-data-role="error"]')!.textContent).toContain('还不确定')
  })

  it('各列种类不同（没有顶层 error）：在列角色下面、不进「需要你确认」，当前值「各列不同」；选一种发 answers.error，所有列一起换', async () => {
    seed({ error_columns: cols(['sd', 'sem']) })
    propose.mockResolvedValue({
      kind: 'candidates', session: 'S2', dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [], error_confirmed: true,
      items: [item('n0', 'line_err', { error: 'ci95', error_source: 'column', error_columns: cols(['ci95', 'ci95'], [false, false]) })],
      roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: [] } },
    } as AiDataProposeResponse)
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')).toBeNull()
    const row = q('[data-data-roles] [data-data-role="error"]')!
    expect(row.textContent).toContain('各列不同')
    await choose('95% 置信区间')
    expect(propose.mock.calls[0]![0].answers).toEqual({ error: 'ci95' })
    expect(q('[data-data-roles] [data-data-role="error"]')!.textContent).toContain('95% 置信区间')
    expect(q('[data-data-confirm-title]')).toBeNull()
  })
})

describe('合画的已汇总表', () => {
  const roles = { x: 'x', y: 'y', yAll: ['y'], group: '文件', stack: false, yExtra: 0, options: { x: ['x'], y: ['y'], group: ['文件'] } }
  it('标题是「N 个文件」；误差线一行、依据句照常（列名只当文本）', async () => {
    useDataFigureStore.setState({
      selected: { path: 'a.csv', sheet: null }, group: [{ path: 'a.csv', sheet: null }, { path: 'b.csv', sheet: null }], draftOpen: true, draftActive: true, phase: 'ready', session: 'S1', roles: roles as never,
      candidates: candidatesFromResponse([item('c0', 'bar_err', { error: 'sem', error_source: 'column', error_column: '<b>sem</b>', error_stated: true, facts: facts() })], 'pa'), thumbs: { c0: 'ready' },
      errorConfirmed: false, designAsked: false, answers: {}, bindingCaptured: true,
    })
    await mount(<><DataPanel /><CandidateStage items={useDataFigureStore.getState().candidates} thumbs={{ c0: 'ready' }} drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} /></>)
    expect(q('[data-data-panel] h2')!.textContent).toBe('2 个文件')
    expect(q('[data-data-roles] [data-data-role="error"]')!.textContent).toContain('标准误 (SEM)')
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-candidate-basis]')!.textContent).toContain('误差线取自「<b>sem</b>」列')
    expect(q('[data-candidate-basis] b')).toBeNull()
  })
})
