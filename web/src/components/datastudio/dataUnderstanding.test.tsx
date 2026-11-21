/**
 * 「读懂数据」前端（ADR 0178 第 1 步）：propose 响应里的 `understanding` → 轮询 → 自动套用 / 胶囊 / 静默。
 * 轮询用假定时器；接口全 mock，不发网络请求。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import {
  aiDataFigureCommit,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataAnswerPatch,
  type AiDataOverview,
  type AiDataUnderstanding,
} from '@/lib/api'
import { diffSuggestion, suggestionFrom, type Suggestion } from '@/lib/dataUnderstanding'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { DataPanel } from './DataPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  aiDataUnderstanding: vi.fn(),
  aiDataProgressCancel: vi.fn(async () => ({ cancelled: false, state: 'none' })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))

let root: Root
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)

const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const commit = vi.mocked(aiDataFigureCommit)
const poll = vi.mocked(aiDataUnderstanding)

const ov = (): AiDataOverview => ({
  path: 'growth.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 144, columns: 4, layout: null,
  fields: [
    { name: 'day', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false },
    { name: 'tumor_volume', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false },
  ],
  preview_rows: [], truncated: false, lead_lines: [], raw_lines: [],
})
const tbl = (path: string) => ({ path, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 3, rows: 144 } })
const roles = { x: 'day', y: ['tumor_volume'], group: 'group', facet: null, unit: null, options: { x: ['day', 'week'], y: ['tumor_volume', 'day'], group: ['group', 'arm'] } }
const cands = (extra: Record<string, unknown> = {}) => ({
  kind: 'candidates' as const, session: 'S1', dropped: [], render: 'lazy' as const, header_row: 1, roles, defaults: [], mixed_cells: [],
  items: [{ id: 'i1', title_code: 'line_band', why_code: 'line_band', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', thumbnail_status: 'pending' as const }],
  ...extra,
})
const pending: AiDataUnderstanding = { id: 'U1', state: 'pending' }
const ready = (answers: AiDataAnswerPatch, extra: Partial<AiDataUnderstanding> = {}): AiDataUnderstanding => ({ id: 'U1', state: 'ready', answers, header_rows: {}, roles: [], dropped: 0, ...extra })

const store = () => useDataFigureStore.getState()
const tick = async (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const pick = async (name: string) => { await act(async () => store().select({ path: name, sheet: null })); await settle(); await settle() }
const proposeCalls = () => propose.mock.calls.length

beforeEach(async () => {
  vi.useFakeTimers()
  for (const f of [files, overview, propose, commit, poll]) f.mockReset()
  setCurrentProjectId('pa')
  store().clear()
  useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
  files.mockResolvedValue({ files: [tbl('growth.csv'), tbl('other.csv')], truncated: false })
  overview.mockResolvedValue(ov())
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => { root.render(<TooltipProvider><DataPanel /></TooltipProvider>) })
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  store().clear()
  vi.useRealTimers()
})

describe('自动套用：用户还没动过下拉', () => {
  it('pending → 轮询到 ready 且建议不同：不弹任何东西，按建议重新出候选；等待那一行灰字随之消失', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands())
    poll.mockResolvedValueOnce(pending).mockResolvedValueOnce(ready({ x: 'week' }))
    await pick('growth.csv')
    expect(q('[data-data-reading]')).not.toBeNull()
    expect(q('[data-data-suggest]')).toBeNull()
    await tick()
    expect(poll).toHaveBeenCalledTimes(1)
    expect(proposeCalls()).toBe(1)
    await tick()
    expect(proposeCalls()).toBe(2)
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { x: 'week' } })
    expect(q('[data-data-reading]')).toBeNull()
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('响应里直接 ready（缓存命中）：不轮询，当场套用', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: ready({ group: 'arm', y: 'day' }) })).mockResolvedValueOnce(cands())
    await pick('growth.csv')
    await settle()
    expect(poll).not.toHaveBeenCalled()
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { group: 'arm', y: 'day' } })
  })

  it('只套用与此刻不同的项：x 相同、y 不同 → 答案里只有 y', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: ready({ x: 'day', y: 'day' }) })).mockResolvedValueOnce(cands())
    await pick('growth.csv')
    await settle()
    expect(propose.mock.calls[1]![0].answers).toEqual({ y: 'day' })
  })

  it('建议的表头行与这次用的不同：带着 header_rows 和建议的列重新读表出候选', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: ready({ x: 'week' }, { header_rows: { 'growth.csv': 3 } }) })).mockResolvedValueOnce(cands())
    await pick('growth.csv')
    await settle()
    await settle()
    expect(propose.mock.calls[1]![0]).toMatchObject({ header_rows: { 'growth.csv': 3 }, answers: { x: 'week' } })
  })
})

describe('动过下拉之后：不覆盖，只出胶囊', () => {
  it('等待期间用户改了分组：ready 到了不自动套用，标题行出胶囊；点一下才套用（用户那一项保留）', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands()).mockResolvedValueOnce(cands())
    poll.mockResolvedValueOnce(ready({ x: 'week' }))
    await pick('growth.csv')
    await act(async () => store().setRole('group', 'arm'))
    await settle()
    expect(proposeCalls()).toBe(2)
    await tick()
    expect(proposeCalls()).toBe(2) // 没有自动重新出候选
    const pill = q('[data-data-suggest]')!
    expect(pill).not.toBeNull()
    expect(pill.textContent).toBe('试试建议')
    expect(pill.getAttribute('title')).toBe('横轴 week')
    expect(q('[data-data-reading]')).toBeNull()
    await act(async () => pill.click())
    await settle()
    expect(propose.mock.calls[2]![0]).toMatchObject({ answers: { group: 'arm', x: 'week' } })
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('不点就是忽略：用户自己把映射改成和建议一样，胶囊自己消失', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValue(cands())
    poll.mockResolvedValueOnce(ready({ x: 'week' }))
    await pick('growth.csv')
    await act(async () => store().setRole('group', 'arm'))
    await tick()
    expect(q('[data-data-suggest]')).not.toBeNull()
    propose.mockResolvedValue(cands({ roles: { ...roles, x: 'week', group: 'arm' } }))
    await act(async () => store().setRole('x', 'week'))
    await settle()
    expect(q('[data-data-suggest]')).toBeNull()
  })
})

describe('静默与不套用', () => {
  it.each([['failed', { id: 'U1', state: 'failed' as const, reason: 'timeout' }], ['off', { id: 'U1', state: 'off' as const }]])('%s：界面与没有这个功能时一样', async (_n, view) => {
    propose.mockResolvedValue(cands({ understanding: pending }))
    poll.mockResolvedValueOnce(view)
    await pick('growth.csv')
    await tick()
    expect(proposeCalls()).toBe(1)
    expect(q('[data-data-reading]')).toBeNull()
    expect(q('[data-data-suggest]')).toBeNull()
    await tick(5000)
    expect(poll).toHaveBeenCalledTimes(1) // 终态之后不再问
  })

  it('一直 pending（后端也超时没回）：到时间上限（75 秒）就停，灰字消失，不报错', async () => {
    propose.mockResolvedValue(cands({ understanding: pending }))
    poll.mockResolvedValue(pending)
    await pick('growth.csv')
    await tick(40_000)
    expect(q('[data-data-reading]')).not.toBeNull() // 订阅登录的后端限时 60 秒：这时还在等
    await tick(40_000)
    expect(poll.mock.calls.length).toBeGreaterThanOrEqual(70)
    expect(poll.mock.calls.length).toBeLessThanOrEqual(76)
    expect(q('[data-data-reading]')).toBeNull()
    expect(q('[data-data-suggest]')).toBeNull()
    expect(proposeCalls()).toBe(1)
  })

  it('轮询偶发网络失败不是结局：下一次接着问', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands())
    poll.mockRejectedValueOnce(new Error('net')).mockResolvedValueOnce(ready({ x: 'week' }))
    await pick('growth.csv')
    await tick(2000)
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { x: 'week' } })
  })

  it('建议与当前相同：什么都不显示、不重新出候选', async () => {
    propose.mockResolvedValue(cands({ understanding: ready({ x: 'day', y: 'tumor_volume', group: 'group' }) }))
    await pick('growth.csv')
    await settle()
    expect(proposeCalls()).toBe(1)
    expect(q('[data-data-suggest]')).toBeNull()
    expect(q('[data-data-reading]')).toBeNull()
  })

  it('建议的列不在下拉里：不算建议（胶囊点了也显示不出来）', async () => {
    propose.mockResolvedValue(cands({ understanding: ready({ x: 'nope' }) }))
    await pick('growth.csv')
    await settle()
    expect(proposeCalls()).toBe(1)
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('换文件：旧文件的结果不套到新文件上，旧轮询停掉', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands())
    poll.mockResolvedValue(ready({ x: 'week' }))
    await pick('growth.csv')
    await pick('other.csv')
    expect(proposeCalls()).toBe(2)
    await tick(5000)
    expect(poll).not.toHaveBeenCalled()
    expect(proposeCalls()).toBe(2)
    expect(q('[data-data-reading]')).toBeNull()
  })

  it('请求在途时换文件：旧的那次响应回来也不套用', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands())
    let release: (v: AiDataUnderstanding) => void = () => {}
    poll.mockImplementationOnce(() => new Promise((r) => { release = r }))
    await pick('growth.csv')
    await tick()
    expect(poll).toHaveBeenCalledTimes(1)
    await pick('other.csv')
    await act(async () => release(ready({ x: 'week' })))
    expect(proposeCalls()).toBe(2)
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('关掉临时标签 / 换项目：轮询停', async () => {
    propose.mockResolvedValue(cands({ understanding: pending }))
    poll.mockResolvedValue(pending)
    await pick('growth.csv')
    await act(async () => store().closeDraft())
    await tick(5000)
    expect(poll).not.toHaveBeenCalled()
    await pick('growth.csv')
    await tick()
    expect(poll).toHaveBeenCalledTimes(1)
    await act(async () => store().clear())
    await tick(5000)
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it('读取中途取消（与大表读取进度 / 取消共存）：轮询停、灰字消失、旧结果不套用，之后重读仍能重新起轮询', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending }))
    poll.mockResolvedValue(ready({ x: 'week' }))
    await pick('growth.csv')
    expect(q('[data-data-reading]')).not.toBeNull()
    // 用户改了分组 → 重新出候选，这一次一直在读
    propose.mockImplementationOnce(() => new Promise(() => {}))
    await act(async () => store().setRole('group', 'arm'))
    await settle()
    expect(store().phase).toBe('loading')
    await act(async () => store().cancelRead())
    await settle()
    expect(store().phase).toBe('cancelled')
    expect(store().understanding).toBe(false)
    await tick(5000)
    expect(poll).not.toHaveBeenCalled()
    expect(q('[data-data-reading]')).toBeNull()
    expect(q('[data-data-suggest]')).toBeNull()
    expect(store().suggestion).toBeNull()
  })

  it('响应里没有 understanding（没连模型）：与从前一样，一次请求、没有灰字', async () => {
    propose.mockResolvedValue(cands())
    await pick('growth.csv')
    await tick(3000)
    expect(poll).not.toHaveBeenCalled()
    expect(q('[data-data-reading]')).toBeNull()
  })
})

describe('lib/dataUnderstanding', () => {
  const r = { x: 'day', y: 'v', yAll: ['v'], stack: false, yExtra: 0, group: null, options: { x: ['day', 'wk'], y: ['v', 'w'], group: ['g'] } }
  it('suggestionFrom：只有 ready 才有；y 给数组取第一个；空建议 = null', () => {
    expect(suggestionFrom({ id: 'u', state: 'failed' }, 'a.csv', true)).toBeNull()
    expect(suggestionFrom(ready({ y: ['w'] }), 'a.csv', true)).toEqual({ picks: { y: 'w' }, headerRow: null })
    expect(suggestionFrom(ready({}), 'a.csv', true)).toBeNull()
    expect(suggestionFrom(ready({}, { header_rows: { 'a.csv': 4 } }), 'a.csv', true)).toEqual({ picks: {}, headerRow: 4 })
    expect(suggestionFrom(ready({}, { header_rows: { 'a.csv': 4 } }), 'a.csv', false)).toBeNull() // 合画不用表头建议
  })
  it('diffSuggestion：none 的含义（x 不要横轴 / 不分组）；y 的 none 不算；用户指过表头行不覆盖', () => {
    expect(diffSuggestion({ picks: { x: 'none' }, headerRow: null }, r, 1, false)).toEqual({ picks: { x: 'none' }, headerRow: null })
    expect(diffSuggestion({ picks: { group: 'none' }, headerRow: null }, r, 1, false)).toBeNull() // 本来就不分组
    expect(diffSuggestion({ picks: { y: 'none' }, headerRow: null }, r, 1, false)).toBeNull()
    expect(diffSuggestion({ picks: {}, headerRow: 3 }, r, 1, true)).toBeNull()
    expect(diffSuggestion({ picks: {}, headerRow: 3 }, r, 1, false)).toEqual({ picks: {}, headerRow: 3 })
    expect(diffSuggestion({ picks: {}, headerRow: 1 }, r, 1, false)).toBeNull()
  })
  it('diffSuggestion：纵轴此刻不止一列而建议只要其中第一列，也算不同（否则多出来的那列一直被画成测量值）', () => {
    const wide = { x: 'time', y: 'signal', yAll: ['signal', 'batch'], stack: false, yExtra: 1, group: null, options: { x: [], y: ['signal', 'batch'], group: [] } }
    expect(diffSuggestion({ picks: { y: 'signal' }, headerRow: null }, wide, 1, false)).toEqual({ picks: { y: 'signal' }, headerRow: null })
    expect(diffSuggestion({ picks: { y: 'signal' }, headerRow: null }, { ...wide, yAll: ['signal'], yExtra: 0 }, 1, false)).toBeNull()
  })
  it('diffSuggestion：成对换轴（纵轴换成 a、原来的纵轴 c 去当横轴）两半都留下——横轴下拉此刻不列当前纵轴，不能据此把它丢掉', () => {
    const abc = { x: 'a', y: 'c', yAll: ['c'], stack: false, yExtra: 0, group: null, options: { x: ['a', 'b'], y: ['a', 'b', 'c'], group: [] } }
    expect(diffSuggestion({ picks: { y: 'a', x: 'c' }, headerRow: null }, abc, 1, false)).toEqual({ picks: { y: 'a', x: 'c' }, headerRow: null })
    expect(diffSuggestion({ picks: { x: 'zzz' }, headerRow: null }, abc, 1, false)).toBeNull() // 这张表里没有的列仍不算
  })
})

const wideRoles = (over: Record<string, unknown> = {}) => ({
  ...roles,
  options: { x: ['day', 'week'], y: ['tumor_volume', 'day', 'a', 'b', 'c'], group: ['group', 'arm'] },
  ...over,
})
const stackRoles = wideRoles({ x: null, y: ['a', 'b', 'c'], group: null, stack: true })
const yTrigger = () => q('[aria-label="纵轴"]')!
const xTrigger = () => q('[aria-label="横轴"]')!

describe('x_measure：不对应下拉的键', () => {
  const same = { x: 'day', y: 'tumor_volume', group: 'group' }
  it('x / y / group 都相同、只有 x_measure：没动过下拉就自动重新出候选一次，请求体里带 x_measure；套用后同一条建议不再触发', async () => {
    propose.mockResolvedValue(cands({ understanding: ready({ ...same, x_measure: 'day' }) }))
    await pick('growth.csv')
    await settle()
    await settle()
    expect(proposeCalls()).toBe(2)
    expect(propose.mock.calls[1]![0].answers).toEqual({ x_measure: 'day' })
    await tick(5000)
    expect(proposeCalls()).toBe(2) // 防循环：第二次响应里又带同一份建议，答案里已经有同值的 x_measure = 相同
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('动过下拉：出胶囊（没有悬停说明），点了才带上 x_measure', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValue(cands())
    poll.mockResolvedValueOnce(ready({ ...same, x_measure: 'day' }))
    await pick('growth.csv')
    await act(async () => store().setRole('group', 'arm'))
    await settle()
    await tick()
    const pill = q('[data-data-suggest]')!
    expect(pill).not.toBeNull()
    expect(pill.getAttribute('title')).toBeNull()
    expect(propose.mock.calls.every((c) => c[0].answers?.x_measure === undefined)).toBe(true)
    await act(async () => pill.click())
    await settle()
    expect(propose.mock.calls.at(-1)![0].answers).toEqual({ group: 'arm', x_measure: 'day' })
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('套用后用户自己改横轴：与横轴绑定的 x_measure 被清掉', async () => {
    propose.mockResolvedValue(cands({ understanding: ready({ ...same, x_measure: 'day' }) }))
    await pick('growth.csv')
    await settle()
    await settle()
    propose.mockResolvedValue(cands())
    await act(async () => store().setRole('x', 'week'))
    await settle()
    expect(propose.mock.calls.at(-1)![0].answers).toEqual({ x: 'week' })
  })

  it('自动套用写进去的键不算「用户动过」：之后换了一份不同的建议仍自动套用', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: ready({ ...same, x_measure: 'day' }) })).mockResolvedValue(cands({ understanding: ready({ ...same, x_measure: 'day', group: 'arm' }) }))
    await pick('growth.csv')
    await settle()
    await settle()
    expect(proposeCalls()).toBeGreaterThanOrEqual(3)
    expect(propose.mock.calls[2]![0].answers).toEqual({ x_measure: 'day', group: 'arm' })
  })
})

describe('多列纵轴', () => {
  it('有横轴的 2 列：数组原样带回；回来的 roles 与建议相同，不再重复触发', async () => {
    propose
      .mockResolvedValueOnce(cands({ roles: wideRoles(), understanding: ready({ y: ['tumor_volume', 'day'] }) }))
      .mockResolvedValue(cands({ roles: wideRoles({ y: ['tumor_volume', 'day'] }), understanding: ready({ y: ['tumor_volume', 'day'] }) }))
    await pick('growth.csv')
    await settle()
    await settle()
    expect(propose.mock.calls[1]![0].answers).toEqual({ y: ['tumor_volume', 'day'] })
    await tick(3000)
    expect(proposeCalls()).toBe(2)
    expect(yTrigger().textContent).toContain('tumor_volume、day')
  })

  it('把列当分组（x == none、3 列）：原样带回；右栏纵轴并列写出、横轴显示「不要横轴」', async () => {
    propose
      .mockResolvedValueOnce(cands({ roles: wideRoles(), understanding: ready({ y: ['a', 'b', 'c'], x: 'none' }) }))
      .mockResolvedValue(cands({ roles: stackRoles, understanding: ready({ y: ['a', 'b', 'c'], x: 'none' }) }))
    await pick('growth.csv')
    await settle()
    await settle()
    expect(propose.mock.calls[1]![0].answers).toEqual({ y: ['a', 'b', 'c'], x: 'none' })
    await tick(3000)
    expect(proposeCalls()).toBe(2)
    expect(yTrigger().textContent).toContain('a、b、c')
    expect(xTrigger().textContent).toContain('不要横轴')
    expect(q('[data-data-suggest]')).toBeNull()
  })

  it('自动套用后用户选单列纵轴：放掉「不要横轴」的绑定；选真横轴：纵轴收回第一列', async () => {
    propose
      .mockResolvedValueOnce(cands({ roles: wideRoles(), understanding: ready({ y: ['a', 'b', 'c'], x: 'none' }) }))
      .mockResolvedValue(cands({ roles: stackRoles }))
    await pick('growth.csv')
    await settle()
    await settle()
    await act(async () => store().setRole('y', 'tumor_volume'))
    await settle()
    expect(propose.mock.calls.at(-1)![0].answers).toEqual({ y: 'tumor_volume' })
    // 回到「把列当分组」的答案，再选真横轴
    propose.mockResolvedValue(cands())
    useDataFigureStore.setState({ answers: { y: ['a', 'b'], x: 'none' } })
    await act(async () => store().setRole('x', 'week'))
    await settle()
    expect(propose.mock.calls.at(-1)![0].answers).toEqual({ y: 'a', x: 'week' })
  })

  it('动过下拉：胶囊的悬停说明把列名用「、」连起来', async () => {
    propose.mockResolvedValueOnce(cands({ roles: wideRoles(), understanding: pending })).mockResolvedValue(cands({ roles: wideRoles() }))
    poll.mockResolvedValueOnce(ready({ y: ['a', 'b'] }))
    await pick('growth.csv')
    await act(async () => store().setRole('group', 'arm'))
    await settle()
    await tick()
    expect(q('[data-data-suggest]')!.getAttribute('title')).toBe('纵轴 a、b')
  })

  it('一列的数组当成那一列；形状不对（有 none / 重复 / 超过 8 列）整项丢掉、不拿第一列凑数', () => {
    expect(suggestionFrom(ready({ y: ['a'] }), 'a.csv', true)).toEqual({ picks: { y: 'a' }, headerRow: null })
    expect(suggestionFrom(ready({ y: ['a', 'b'] }), 'a.csv', true)!.picks.y).toEqual(['a', 'b'])
    expect(suggestionFrom(ready({ y: ['a', 'none'] }), 'a.csv', true)).toBeNull()
    expect(suggestionFrom(ready({ y: ['a', 'a'] }), 'a.csv', true)).toBeNull()
    expect(suggestionFrom(ready({ y: Array.from({ length: 9 }, (_, i) => `c${i}`) }), 'a.csv', true)).toBeNull()
    expect(suggestionFrom(ready({ y: ['a', 3 as unknown as string] }), 'a.csv', true)).toBeNull()
  })
})

describe('lib/dataUnderstanding：新键', () => {
  const base = { x: 'day', y: 'v', yAll: ['v'], stack: false, yExtra: 0, group: null, options: { x: ['day'], y: ['v', 'a', 'b', 'c'], group: [] } }
  const stack = { ...base, x: null, y: 'a', yAll: ['a', 'b', 'c'], yExtra: 2, stack: true }
  const sug = (picks: Suggestion['picks'], xMeasure?: string): Suggestion => ({ picks, ...(xMeasure !== undefined ? { xMeasure } : {}), headerRow: null })
  it('suggestionFrom 带出 x_measure；cells 与未知键不套用', () => {
    expect(suggestionFrom(ready({ x_measure: 'day', cells: { v: {} }, foo: 1 }), 'a.csv', true)).toEqual({ picks: {}, xMeasure: 'day', headerRow: null })
  })
  it('数组 y：与此刻纵轴同序 = 相同；顺序不同 = 不同（线的顺序 / 把列当分组时的类别顺序）；有选不到的列 = 整条不算', () => {
    expect(diffSuggestion(sug({ y: ['a', 'b', 'c'], x: 'none' }), stack, 1, false)).toBeNull()
    expect(diffSuggestion(sug({ y: ['c', 'b', 'a'], x: 'none' }), stack, 1, false)).toEqual({ picks: { y: ['c', 'b', 'a'] }, headerRow: null })
    expect(diffSuggestion(sug({ y: ['a', 'b'] }), base, 1, false)).toEqual({ picks: { y: ['a', 'b'] }, headerRow: null })
    expect(diffSuggestion(sug({ y: ['a', 'zzz'] }), base, 1, false)).toBeNull()
  })
  it('数组 y 已经原样发过（后端没采纳）：不再算不同', () => {
    expect(diffSuggestion(sug({ y: ['a', 'b'] }), base, 1, false, { y: ['a', 'b'] })).toBeNull()
  })
  it('x_measure：答案里没有同值的就算不同（即使 x / y / group 都相同）；有了就相同；列不在表里不算', () => {
    expect(diffSuggestion(sug({ x: 'day', y: 'v' }, 'day'), base, 1, false)).toEqual({ picks: {}, xMeasure: 'day', headerRow: null })
    expect(diffSuggestion(sug({}, 'day'), base, 1, false, { x_measure: 'day' })).toBeNull()
    expect(diffSuggestion(sug({}, 'zzz'), base, 1, false)).toBeNull()
  })
  it('换表头行的分支也带上 x_measure', () => {
    expect(diffSuggestion({ picks: {}, xMeasure: 'day', headerRow: 3 }, base, 1, false)).toEqual({ picks: {}, xMeasure: 'day', headerRow: 3 })
  })
})

describe('轮询到终态为止', () => {
  it('第 45 秒才 ready 也能套用', async () => {
    propose.mockResolvedValueOnce(cands({ understanding: pending })).mockResolvedValueOnce(cands())
    let n = 0
    poll.mockImplementation(async () => (++n >= 45 ? ready({ x: 'week' }) : pending))
    await pick('growth.csv')
    await tick(44_000)
    expect(proposeCalls()).toBe(1)
    expect(q('[data-data-reading]')).not.toBeNull()
    await tick(2000)
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { x: 'week' } })
  })
  it('卸载 / 换项目即停：之后不再问', async () => {
    propose.mockResolvedValue(cands({ understanding: pending }))
    poll.mockResolvedValue(pending)
    await pick('growth.csv')
    await tick(5000)
    const n = poll.mock.calls.length
    await act(async () => store().clear())
    await tick(20_000)
    expect(poll.mock.calls.length).toBe(n)
  })
})
