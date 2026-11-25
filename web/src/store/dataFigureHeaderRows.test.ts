/**
 * #2232：换「分析用的 AI」时，上一个 AI 建议改的表头行（`autoHeaderRows`）一并回退；用户自己点选的保留；
 * 回退走「先清自动答案、再重新读表」；关了不回退；这次重跑不发（动过下拉 / 出图中）时标记留着，下次重新读表之前再退。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  aiDataChoiceSet,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataOverview,
  type AiDataProposeResponse,
  type AiDataUnderstandingChoice,
} from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { setCurrentProjectId } from '@/lib/session'
import { configureDataFigureHost, useDataFigureStore } from './dataFigureStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  aiDataUnderstanding: vi.fn(),
  aiDataChoiceGet: vi.fn(),
  aiDataChoiceSet: vi.fn(),
}))
const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const choiceSet = vi.mocked(aiDataChoiceSet)
const understanding = vi.mocked(aiDataUnderstanding)
const st = () => useDataFigureStore.getState()
const flush = () => new Promise((r) => setTimeout(r, 0))
const ref = (p: string) => ({ path: p, sheet: null })
const choice = (o: Partial<AiDataUnderstandingChoice> = {}): AiDataUnderstandingChoice => ({ state: 'on', mode: 'auto', connection: 'a', options: [], ...o })
const entry = (p: string) => ({ path: p, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows: 3 } })
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const cands = (key: string, n: number, extra: Record<string, unknown> = {}): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: `S-${key}-${n}`, dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: [{ id: `${key}-${n}-0`, title_code: 'line_band', why_code: 'line_band', recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: '/t', thumbnail_status: 'pending' as const }],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
    ...extra,
  }) as AiDataProposeResponse
const bodyOf = (n: number) => propose.mock.calls[n]![0] as { table?: string; answers?: Record<string, unknown>; header_rows?: Record<string, number> }

/** AI 第一次看某张表（请求里还没有它的表头行）就建议「表头在第 3 行」；带着表头行再问就没有建议了 */
let suggest = true // 换 AI 之后新 AI 这次不再建议改表头行（想看回退本身，不被「又建议了一次」盖住）
const aiSuggestsRow3 = () =>
  propose.mockImplementation(async (b) => {
    const body = b as { table?: string; header_rows?: Record<string, number> }
    const t = body.table!
    const first = suggest && body.header_rows?.[t] === undefined
    return cands(t, propose.mock.calls.length, first ? { understanding: { id: `U-${t}`, state: 'ready', header_rows: { [t]: 3 }, answers: {} } } : {})
  })
const settle = () => st().candidates.forEach((c) => st().thumbSettled(st().session!, c.id, true))
const open = async (p: string) => {
  st().select(ref(p))
  await flush()
  await flush()
  await flush()
  settle()
}
const switchAi = async (c: Partial<AiDataUnderstandingChoice>, newAiSuggests = false) => {
  suggest = newAiSuggests
  choiceSet.mockResolvedValue({ understanding: choice(c) })
  await st().setUnderstandingChoice({ mode: 'connection', connection: c.connection ?? 'b' })
  await flush()
  await flush()
  await flush()
}

beforeEach(() => {
  suggest = true
  resetCommitQueue()
  for (const f of [files, overview, propose, choiceSet, understanding]) f.mockReset()
  overview.mockResolvedValue(ov())
  files.mockResolvedValue({ files: [entry('a.csv'), entry('b.csv')], truncated: false, understanding: choice() })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({ openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
})
afterEach(() => {
  useDataFigureStore.getState().clear()
  vi.useRealTimers()
})

describe('记录哪些表头行是 AI 写的', () => {
  it('套用 AI 建议写 headerRows 并记进 autoHeaderRows；用户自己点选就从记录里删掉', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    expect(st().headerRows).toEqual({ 'a.csv': 3 })
    expect(st().autoHeaderRows).toEqual({ 'a.csv': 3 })
    st().setHeaderRow('a.csv', null, 5)
    await flush()
    expect(st().headerRows['a.csv']).toBe(5)
    expect(st().autoHeaderRows).toEqual({})
  })
})

describe('换 AI：回退 AI 改的表头行', () => {
  it('on → on 换连接：当前这份的表头行回退，先清完自动答案再重新读表（请求里没有 header_rows、也没有旧的自动答案）', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ answers: { y: 'z', error: 'sem' }, autoPicks: { y: 'z' } })
    const before = propose.mock.calls.length
    const ovBefore = overview.mock.calls.length
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' })
    expect(st().headerRows).toEqual({})
    expect(st().autoHeaderRows).toEqual({})
    const sent = bodyOf(before)
    expect(sent.header_rows).toBeUndefined() // 回到后端自己猜的表头行
    expect(sent.answers).toEqual({ error: 'sem' }) // 自动填的 y 清了、用户点的 error 还在（顺序没反）
    expect(overview.mock.calls.length).toBe(ovBefore + 1) // 概览按新键重读一次
    expect(propose.mock.calls.length).toBe(before + 1) // 候选只发一次
  })

  it('用户自己点选的表头行保留（AI 之后又点了别的行 = 用户的）；这时重跑只清自动答案', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    st().setHeaderRow('a.csv', null, 5)
    await flush()
    await flush()
    settle()
    useDataFigureStore.setState({ autoPicks: { y: 'z' }, answers: { y: 'z' } })
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' })
    expect(st().headerRows).toEqual({ 'a.csv': 5 })
    expect(bodyOf(propose.mock.calls.length - 1).header_rows).toEqual({ 'a.csv': 5 })
  })

  it('值已被别处改过（不再等于 AI 写的）：不删', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ headerRows: { 'a.csv': 7 } }) // 记录里还是 3
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' })
    expect(st().headerRows).toEqual({ 'a.csv': 7 })
    expect(st().autoHeaderRows).toEqual({})
  })

  it('变成「关」：不回退（关闭后已由 AI 选好的保持原样）', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    choiceSet.mockResolvedValue({ understanding: choice({ state: 'off', mode: 'off', connection: null }) })
    await st().setUnderstandingChoice({ mode: 'off' })
    expect(st().headerRows).toEqual({ 'a.csv': 3 })
    expect(st().autoHeaderRows).toEqual({ 'a.csv': 3 })
  })

  it('关 → 开（宿主 noteUnavailable）同样回退', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    choiceSet.mockResolvedValue({ understanding: choice({ state: 'off', mode: 'off', connection: null }) })
    await st().setUnderstandingChoice({ mode: 'off' })
    st().noteUnavailable(true)
    suggest = false
    choiceSet.mockResolvedValue({ understanding: choice({ connection: 'b' }) })
    await st().setUnderstandingChoice({ mode: 'auto' })
    st().noteUnavailable(false)
    await flush()
    await flush()
    expect(st().headerRows).toEqual({})
  })

  it('加载中换 AI：落定后的补跑也回退', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReset()
    propose.mockReturnValueOnce(new Promise((r) => (release = r)))
    useDataFigureStore.setState({ phase: 'ready' })
    st().setRole('y', 'z') // 触发一次在途的出候选（loading）
    await flush()
    expect(st().phase).toBe('loading')
    useDataFigureStore.setState({ answers: {}, autoPicks: {} })
    propose.mockImplementation(async () => cands('a.csv', 99))
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' })
    expect(st().headerRows).toEqual({ 'a.csv': 3 }) // 请求还在途：先不动
    release(cands('a.csv', 98))
    await flush()
    await flush()
    await flush()
    expect(st().headerRows).toEqual({})
  })
})

describe('A、B 两张表都被 AI 改过表头行并留存', () => {
  it('换 AI 后 headerRows 里两条都没了；切回 B 不命中留存、按新 AI 重新读表', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv') // A：AI 改了表头行
    await open('b.csv') // 留下 A；B：AI 也改了
    expect(st().headerRows).toEqual({ 'a.csv': 3, 'b.csv': 3 })
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' }) // 当前是 B：重跑
    expect(st().headerRows).toEqual({}) // 两条都没了
    settle()
    // 切到 A：留存的 A（表头行 3、旧 AI）不命中，按新 AI 重新读
    const n = propose.mock.calls.length
    const ovN = overview.mock.calls.length
    st().select(ref('a.csv'))
    await flush()
    await flush()
    await flush()
    expect(propose.mock.calls.length).toBeGreaterThan(n)
    expect(overview.mock.calls.length).toBeGreaterThan(ovN)
    expect(bodyOf(n).table).toBe('a.csv')
  })
})

describe('这一次重跑不发（动过下拉 / 正在出图）', () => {
  it('不回退、不重跑；标记留着，下次重新读表之前再退（不会让新 AI 沿用旧 AI 的表头行）', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ drawingId: 'x' }) // 正在出图
    const before = propose.mock.calls.length
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' }, true)
    expect(propose.mock.calls.length).toBe(before)
    expect(st().headerRows).toEqual({ 'a.csv': 3 }) // 当前这份还是旧 AI 的分析，自洽
    // 出图结束、这份草稿没了；之后打开别的 / 同一张表：读表之前先退
    useDataFigureStore.setState({ drawingId: null, phase: 'idle', selected: null, draftOpen: false })
    st().select(ref('a.csv'))
    await flush()
    await flush()
    await flush()
    expect(bodyOf(before).header_rows?.['a.csv']).toBeUndefined()
    expect(st().autoHeaderRows).toEqual({ 'a.csv': 3 }) // 新 AI 又建议了第 3 行，重新记
  })

  it('用户动过下拉：同样不动', async () => {
    aiSuggestsRow3()
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ answers: { x: 'mine' } })
    const before = propose.mock.calls.length
    await switchAi({ mode: 'connection', chosen: 'b', connection: 'b' })
    expect(propose.mock.calls.length).toBe(before)
    expect(st().headerRows).toEqual({ 'a.csv': 3 })
  })
})
