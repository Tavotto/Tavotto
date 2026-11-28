/**
 * 表里现成的误差列画成误差线：两种新画法的名字 / 说明（列名只当文本）、依据句（含算出来的误差线缺了几处，位置只在 title 里）、
 * 右栏「误差线」一行的三种情形（unknown 待确认 / 列名自己说明了 / 算出来的与以前一样）。
 */
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
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)]
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

const item = (id: string, code: string, extra: Record<string, unknown> = {}) => ({
  id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...extra,
})
const facts = (o: Record<string, unknown> = {}) => ({ rows: 12, drawn: 12, groups: 4, n_min: 3, n_max: 3, x_values: 4, ...o })

describe('主图：两种新画法', () => {
  const show = (code: string, extra: Record<string, unknown> = {}) => {
    const items = candidatesFromResponse([item('a', code, { facts: facts(), error: 'unknown', error_source: 'column', error_column: '<b>SD</b> of 值', ...extra })], 'pa')
    return mount(<CandidateStage items={items} thumbs={{ a: 'ready' }} drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
  }

  it('points_err：标题「点 + 误差线」，说明不重复列名（列名在依据句里，只当文本）', async () => {
    await show('points_err')
    expect(q('[data-candidate-title]')!.textContent).toBe('点 + 误差线')
    expect(q('[data-candidate-why]')!.textContent).toBe('表里已经是各组的均值，直接画出来。')
    expect(q('[data-candidate-why] b')).toBeNull()
  })

  it('line_err：标题「点 + 折线 + 误差线」；说明不写误差种类', async () => {
    await show('line_err')
    expect(q('[data-candidate-title]')!.textContent).toBe('点 + 折线 + 误差线')
    expect(q('[data-candidate-why]')!.textContent).toBe('表里已经是每个位置的均值，按顺序连起来。')
    expect(q('[data-candidate-why]')!.textContent).not.toMatch(/标准差|标准误|置信/)
  })

  it('依据句：按 N 行绘制，每行一个点；误差线取自「列」列；没画的行接在后面', async () => {
    await show('points_err', { facts: facts({ skipped_missing: 1, skipped_other: 0 }), unplotted: { count: 1, code: null } })
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 12 行绘制，每行一个点；误差线取自「<b>SD</b> of 值」列；另有 1 行因缺值未计入。')
    expect(q('[data-candidate-basis]')!.getAttribute('title')).toBeNull()
  })
})

describe('主图：算出来的误差线缺了几处', () => {
  const show = (code: string, missing: string[]) => {
    const items = candidatesFromResponse([item('a', code, { facts: facts(), error: 'sd', error_source: 'computed', error_missing_for: missing })], 'pa')
    return mount(<CandidateStage items={items} thumbs={{ a: 'ready' }} drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
  }

  it('bar_error：句末接「N 处只有 1 个数值，算不出误差线」，位置只在 title 里（「、」连）；灰字仍是同一个 p', async () => {
    await show('bar_error', ['A 组', 'B 组'])
    const p = q('[data-candidate-basis]')!
    expect(p.textContent).toBe('依据：按 4 组各 3 个数值绘制；2 处只有 1 个数值，算不出误差线。')
    expect(p.getAttribute('title')).toBe('A 组、B 组')
    expect(qa('[data-candidate-basis]')).toHaveLength(1)
  })

  it('只有一处：句子里直接点名，不再用 title 重复', async () => {
    await show('bar_error', ['C'])
    const p = q('[data-candidate-basis]')!
    expect(p.textContent).toBe('依据：按 4 组各 3 个数值绘制；「C」只有 1 个数值，算不出误差线。')
    expect(p.getAttribute('title')).toBeNull()
  })

  it('line_band 写「误差带」；超过 8 个位置截断加「等」', async () => {
    await show('line_band', Array.from({ length: 10 }, (_, i) => `x=${i + 1}`))
    const p = q('[data-candidate-basis]')!
    expect(p.textContent).toContain('10 处只有 1 个数值，算不出误差带')
    expect(p.getAttribute('title')).toBe('x=1、x=2、x=3、x=4、x=5、x=6、x=7、x=8等')
  })

  it('没有缺的位置：没有这一句、没有 title', async () => {
    await show('bar_error', [])
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 4 组各 3 个数值绘制。')
    expect(q('[data-candidate-basis]')!.getAttribute('title')).toBeNull()
  })
})

describe('右栏「误差线」一行', () => {
  const roles = { x: 'x', y: 'y', yAll: ['y'], group: null, stack: false, yExtra: 0, options: { x: ['x'], y: ['y'], group: [] } }
  const resp = (code: string, extra: Record<string, unknown>): AiDataProposeResponse =>
    ({
      kind: 'candidates', session: 'S2', dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
      items: [item('n0', code, { error_source: 'column', error_column: 'e', ...extra })],
      roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: [] } },
    }) as AiDataProposeResponse
  const seed = (code: string, extra: Record<string, unknown>, over: Record<string, unknown> = {}) =>
    useDataFigureStore.setState({
      selected: { path: 'a.csv', sheet: null }, draftOpen: true, draftActive: true, phase: 'ready', session: 'S1', roles: roles as never,
      candidates: candidatesFromResponse([item('c0', code, { error_source: 'column', error_column: 'e', ...extra })], 'pa'), thumbs: { c0: 'ready' },
      errorConfirmed: false, designAsked: false, answers: {}, bindingCaptured: true, ...over,
    })
  beforeEach(() => {
    propose.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
  })

  it('error 是 unknown 且没确认：在「需要你确认」下面，当前值「还不确定」，选项是三种；选了发 answers.error，确认后搬到列角色下面', async () => {
    seed('points_err', { error: 'unknown' })
    propose.mockResolvedValue({ ...resp('points_err', { error: 'sem' }), error_confirmed: true } as AiDataProposeResponse)
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')!.textContent).toBe('需要你确认')
    const row = q('[data-data-confirm] [data-data-role="error"]')!
    expect(row.textContent).toContain('还不确定')
    await act(async () => {
      row.querySelector('button')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    expect(qa('[role=option]').map((o) => o.textContent)).toEqual(['标准差 (SD)', '标准误 (SEM)', '95% 置信区间'])
    await act(async () => {
      const o = qa('[role=option]')[1]!
      o.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
      o.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
    expect(propose.mock.calls[0]![0].answers).toEqual({ error: 'sem' })
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-data-roles] [data-data-role="error"]')!.textContent).toContain('标准误 (SEM)')
  })

  it('列名已声明的现成误差类型不可改，旧请求不能覆盖当前候选', async () => {
    seed('line_err', { error: 'sd', error_stated: true }, { errorConfirmed: true, answers: { error: 'sem' } })
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')).toBeNull()
    const row = q('[data-data-roles] [data-data-role="error"]')!
    expect(row.textContent).toContain('标准差 (SD)')
    expect(row.querySelector<HTMLButtonElement>('button')!.disabled).toBe(true)
    expect(propose).not.toHaveBeenCalled()
  })

  it('算出来的（computed）与以前一样：没确认就在「需要你确认」，当前值是那一种', async () => {
    seed('bar_error', { error: 'sd', error_source: 'computed', error_column: undefined })
    await mount(<DataPanel />)
    const row = q('[data-data-confirm] [data-data-role="error"]')!
    expect(row.textContent).toContain('标准差 (SD)')
    expect(row.textContent).not.toContain('还不确定')
  })

  it('已点选过（error_confirmed + answers.error）：不再待确认，按点选的那一种显示', async () => {
    seed('points_err', { error: 'sem' }, { errorConfirmed: true, answers: { error: 'sem' } })
    await mount(<DataPanel />)
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-data-roles] [data-data-role="error"]')!.textContent).toContain('标准误 (SEM)')
  })
})
