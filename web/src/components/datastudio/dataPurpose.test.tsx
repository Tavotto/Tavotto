/** 右栏「目的」一行与主图下面的「为什么不是这张」：出现条件、文案、点选写 answers、不撑高、不动现有说明。 */
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

describe('右栏「目的」', () => {
  const roles = { x: 'x', y: 'y', yAll: ['y'], group: 'g', stack: false, yExtra: 0, options: { x: ['x'], y: ['y'], group: ['g'] } }
  const seed = (purpose: unknown) =>
    useDataFigureStore.setState({
      selected: { path: 'a.csv', sheet: null }, draftOpen: true, draftActive: true, phase: 'ready', session: 'S1', roles: roles as never,
      candidates: candidatesFromResponse([item('c0', 'points_mean')], 'pa'), thumbs: { c0: 'ready' }, purpose: purpose as never, answers: {}, bindingCaptured: true,
    })
  const choose = async (value: string) => {
    await act(async () => {
      q('[data-data-role="purpose"] button')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    const opt = qa('[role=option]').find((o) => o.textContent === value)
    expect(opt, `没有选项 ${value}`).toBeTruthy()
    await act(async () => {
      opt!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
      opt!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
  }
  beforeEach(() => {
    propose.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
  })

  it('选项来自响应，文案是「比较各组 / 看趋势 / 看关系 / 看分布」；当前值是 purpose.code；在分组之后、误差线之前；source 不区分（没有灰字）', async () => {
    seed({ code: 'trend', source: 'data', options: ['compare_groups', 'trend', 'relationship', 'distribution'] })
    await mount(<DataPanel />)
    const row = q('[data-data-role="purpose"]')!
    expect(row.textContent).toContain('目的')
    expect(row.textContent).toContain('看趋势')
    const order = qa('[data-data-role]').map((e) => e.getAttribute('data-data-role'))
    expect(order.indexOf('purpose')).toBeGreaterThan(order.indexOf('group'))
    // source 换成 user：这一行一字不变
    const before = row.textContent
    act(() => useDataFigureStore.setState({ purpose: { code: 'trend', source: 'user', options: ['compare_groups', 'trend', 'relationship', 'distribution'] } }))
    expect(q('[data-data-role="purpose"]')!.textContent).toBe(before)
    expect(q('[data-data-confirm]')).toBeNull() // 不进「需要你确认」
    await act(async () => {
      q('[data-data-role="purpose"] button')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    expect(qa('[role=option]').map((o) => o.textContent)).toEqual(['比较各组', '看趋势', '看关系', '看分布'])
  })

  it('没有 purpose / 选项不足两个 / code 为 null：不出这一行', async () => {
    for (const p of [null, { code: 'trend', source: 'data', options: [] }, { code: 'trend', source: 'data', options: ['trend'] }, { code: null, source: 'data', options: ['trend', 'compare_groups'] }]) {
      seed(p)
      await mount(<DataPanel />)
      expect(q('[data-data-role="purpose"]'), JSON.stringify(p)).toBeNull()
      await act(async () => root.unmount())
      host.remove()
    }
  })

  it('当前值万一不在选项里：补进下拉，不会显示一个选不到的值', async () => {
    seed({ code: 'distribution', source: 'data', options: ['compare_groups', 'trend'] })
    await mount(<DataPanel />)
    expect(q('[data-data-role="purpose"]')!.textContent).toContain('看分布')
    await act(async () => {
      q('[data-data-role="purpose"] button')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    expect(qa('[role=option]').map((o) => o.textContent)).toContain('看分布')
  })

  it('点选：写 answers.purpose 重新出候选、立刻显示所选（等的时候），回来以响应为准', async () => {
    seed({ code: 'compare_groups', source: 'data', options: ['compare_groups', 'trend'] })
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValue(new Promise((r) => (release = r)))
    await mount(<DataPanel />)
    await choose('看趋势')
    expect(propose.mock.calls[0]![0].answers).toEqual({ purpose: 'trend' })
    expect(q('[data-data-role="purpose"]')!.textContent).toContain('看趋势') // 立刻显示所选
    await act(async () =>
      release({
        kind: 'candidates', session: 'S2', dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
        items: [item('n0', 'line_band')], roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: [] } },
        purpose: { code: 'compare_groups', source: 'data', options: ['compare_groups', 'trend'] }, // 后端当没点
      } as AiDataProposeResponse),
    )
    await flush()
    expect(q('[data-data-role="purpose"]')!.textContent).toContain('比较各组') // 以响应为准
    expect(useDataFigureStore.getState().autoPicks).toEqual({})
  })
})

describe('主图下面「为什么不是这张」', () => {
  const show = (it: Record<string, unknown>, purposeCode: string | null = 'compare_groups') => {
    const items = candidatesFromResponse([item('a', 'line_band', { facts: { rows: 4, drawn: 4, groups: 2, n_min: 2, n_max: 2, x_values: 2 }, ...it })], 'pa')
    return mount(<CandidateStage items={items} thumbs={{ a: 'ready' }} drawingId={null} previewKey={null} purposeCode={purposeCode} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
  }
  const img = () => q<HTMLImageElement>('[data-candidate-image] img')!

  it.each([
    ['summary_hides_spread', '柱和误差线通常只代表均值与离散程度，不显示每个观测值。'],
    ['line_needs_order', '连线通常代表连续变化的量。'],
    ['order_prefers_line', '横轴有顺序，连线更能看出走势。'],
    ['answers_other_purpose', '你选的是「比较各组」，这张回答的是另一个问题。'],
  ])('%s：独立一行、不带开头，在「依据」之后', async (code, text) => {
    await show({ why_not: { code, guidance: ['G-018'], tier: 'perception' } })
    const p = q(`[data-candidate-whynot="${code}"]`)!
    expect(p.textContent).toBe(text)
    expect(p.tagName).toBe('P')
    const basis = q('[data-candidate-basis]')!
    expect(basis.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(p.className).toContain('text-xs')
    expect(p.className).toContain('text-ink-3')
    expect(p.className).not.toMatch(/\bbg-/)
  })

  it('没有 why_not：不出，也不占位（主图最大高度算式不变）；有：主图让出这一行', async () => {
    await show({})
    expect(q('[data-candidate-whynot]')).toBeNull()
    act(() => img().dispatchEvent(new Event('load')))
    expect(img().style.maxHeight).toContain('100cqh - 250px')
    await act(async () => root.unmount())
    host.remove()
    await show({ why_not: { code: 'order_prefers_line', guidance: [], tier: 'perception' } })
    act(() => img().dispatchEvent(new Event('load')))
    expect(img().style.maxHeight).toContain('100cqh - 270px')
  })

  it('bar_error 现有的说明句（个别点会藏在柱里）一字不改，与这句并存', async () => {
    await show({ title_code: 'bar_error', why_code: 'bar_error', error: 'sd', why_not: { code: 'summary_hides_spread', guidance: [], tier: 'data' } })
    expect(q('[data-candidate-why]')!.textContent).toContain('个别点会藏在柱里')
    expect(q('[data-candidate-whynot]')!.textContent).toBe('柱和误差线通常只代表均值与离散程度，不显示每个观测值。')
  })
})
