/**
 * 「目的」（`answers.purpose` / 响应的 `purpose`）的 store 侧：响应对象存进草稿；只由右栏点选写入、不进 autoPicks；
 * 换 AI 重跑清自动答案时它保留；改列角色、套用 AI 建议后仍带着发；留存草稿还原后目的与候选顺序和切走前一致。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { aiDataFigureDiscard, aiDataFigurePropose, aiDataFilesAll, aiDataOverview, aiDataUnderstanding, type AiDataOverview, type AiDataProposeResponse } from '@/lib/api'
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
}))
const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const discard = vi.mocked(aiDataFigureDiscard)
const understanding = vi.mocked(aiDataUnderstanding)
const st = () => useDataFigureStore.getState()
const flush = () => new Promise((r) => setTimeout(r, 0))
const ref = (p: string) => ({ path: p, sheet: null })
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const item = (id: string, code: string) => ({ id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const })
const resp = (s: string, codes: string[], extra: Record<string, unknown> = {}): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: s, dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: codes.map((c, i) => item(`${s}-${i}`, c)),
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
    ...extra,
  }) as AiDataProposeResponse
const P = (code: string | null, source: 'user' | 'data' = 'data', options = ['compare_groups', 'trend']) => ({ purpose: { code, source, options } })
const bodyOf = (n: number) => propose.mock.calls[n]![0]
const run = async () => {
  st().select(ref('a.csv'))
  await flush()
  await flush()
}

beforeEach(() => {
  resetCommitQueue()
  for (const f of [files, overview, propose, discard, understanding]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  overview.mockResolvedValue(ov())
  files.mockResolvedValue({ files: [{ path: 'a.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } }, { path: 'b.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } }], truncated: false })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({ openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
})
afterEach(() => {
  useDataFigureStore.getState().clear()
  vi.useRealTimers()
})

describe('响应里的 purpose', () => {
  it('存进草稿状态；没有 / code 为 null：null', async () => {
    propose.mockResolvedValueOnce(resp('S1', ['points_mean', 'line_band'], P('compare_groups')))
    await run()
    expect(st().purpose).toEqual({ code: 'compare_groups', source: 'data', options: ['compare_groups', 'trend'] })
    propose.mockResolvedValueOnce(resp('S2', ['points_mean'], P(null)))
    st().setConfirm('error', 'sd')
    await flush()
    expect(st().purpose).toBeNull()
    propose.mockResolvedValueOnce(resp('S3', ['points_mean']))
    st().setConfirm('error', 'sem')
    await flush()
    expect(st().purpose).toBeNull()
  })
})

describe('setConfirm("purpose")', () => {
  it('把 answers.purpose 写进去重新出候选；不进 autoPicks；不算动了列角色下拉', async () => {
    propose.mockResolvedValueOnce(resp('S1', ['points_mean', 'line_band'], P('compare_groups'))).mockResolvedValueOnce(resp('S2', ['line_band', 'points_mean'], P('trend', 'user')))
    await run()
    st().setConfirm('purpose', 'trend')
    await flush()
    expect(bodyOf(1).answers).toEqual({ purpose: 'trend' })
    expect(st().autoPicks).toEqual({})
    expect(st().candidates[0]!.title_code).toBe('line_band')
    expect(st().purpose?.source).toBe('user')
    // 点的和此刻已经点过的一样：什么都不发
    st().setConfirm('purpose', 'trend')
    await flush()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('点的就是此刻显示的（假设的目的）：也算确认，会发', async () => {
    propose.mockResolvedValue(resp('S1', ['points_mean', 'line_band'], P('compare_groups')))
    await run()
    st().setConfirm('purpose', 'compare_groups')
    await flush()
    expect(bodyOf(1).answers).toEqual({ purpose: 'compare_groups' })
  })

  it('改列角色（用户动下拉）后仍带着发；界面显示以响应的 purpose.code 为准（后端当没点时 code 回到假设）', async () => {
    propose.mockResolvedValueOnce(resp('S1', ['points_mean', 'line_band'], P('compare_groups'))).mockResolvedValueOnce(resp('S2', ['line_band'], P('trend', 'user'))).mockResolvedValueOnce(resp('S3', ['points_mean'], P('compare_groups', 'data')))
    await run()
    st().setConfirm('purpose', 'trend')
    await flush()
    st().setRole('y', 'z')
    await flush()
    expect(bodyOf(2).answers).toEqual({ purpose: 'trend', y: 'z' }) // 一直带着
    expect(st().purpose?.code).toBe('compare_groups') // 显示的是响应说的（这批没有趋势族，后端当没点）
    expect(st().purpose?.options).toContain(st().purpose!.code!)
  })
})

describe('与 AI 自动填的答案分开', () => {
  it('换 AI 重跑清自动答案时「目的」保留；套用 AI 建议只写 x/y/group，目的不动', async () => {
    propose.mockImplementation(async () => resp(`S${propose.mock.calls.length}`, ['points_mean', 'line_band'], P('compare_groups')))
    await run()
    useDataFigureStore.setState({ answers: { y: 'z', purpose: 'trend' }, autoPicks: { y: 'z' } })
    const n = propose.mock.calls.length
    st().rerunForConnection()
    await flush()
    expect(propose.mock.calls.length).toBe(n + 1)
    expect(bodyOf(n).answers).toEqual({ purpose: 'trend' }) // 自动填的 y 拿掉了，用户点的目的还在
    expect(st().autoPicks).toEqual({})
  })

  it('读懂数据的建议里就算带了 purpose 也不会被自动套用', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    propose.mockResolvedValueOnce(resp('S1', ['points_mean'], { ...P('compare_groups'), understanding: { id: 'U1', state: 'pending' } })).mockResolvedValue(resp('S2', ['points_mean'], P('compare_groups')))
    understanding.mockResolvedValue({ id: 'U1', state: 'ready', answers: { y: 'z', purpose: 'trend' } as never })
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(0)
    const last = propose.mock.calls.at(-1)![0]
    expect(last.answers).toEqual({ y: 'z' })
    expect(st().autoPicks).toEqual({ y: 'z' })
  })
})

describe('留存草稿（切走再切回）', () => {
  it('目的与候选顺序都和切走前一致，不重新出', async () => {
    propose.mockImplementation(async (b) => {
      const reordered = (b as { answers?: { purpose?: string } }).answers?.purpose === 'trend'
      return resp(`S-${(b as { table: string }).table}-${propose.mock.calls.length}`, reordered ? ['line_band', 'points_mean'] : ['points_mean', 'line_band'], P(reordered ? 'trend' : 'compare_groups', reordered ? 'user' : 'data'))
    })
    await st().loadFiles()
    const settle = () => st().candidates.forEach((c) => st().thumbSettled(st().session!, c.id, true))
    st().select(ref('a.csv'))
    await flush()
    await flush()
    st().setConfirm('purpose', 'trend')
    await flush()
    settle()
    const snap = { purpose: st().purpose, order: st().candidates.map((c) => c.title_code), answers: st().answers }
    st().select(ref('b.csv'))
    await flush()
    await flush()
    settle()
    const n = propose.mock.calls.length
    st().select(ref('a.csv'))
    expect(propose.mock.calls.length).toBe(n)
    expect(st().purpose).toEqual(snap.purpose)
    expect(st().candidates.map((c) => c.title_code)).toEqual(snap.order)
    expect(st().answers).toEqual(snap.answers)
  })
})
