/**
 * 「需要你确认」两项（误差线种类 `answers.error`、是否重复测量 `answers.design`）与主图预览（`previewKey`）的 store 侧：
 * 只由用户点击写入、写了重新出候选、不算动了列角色下拉、不重起「读懂数据」；建议 / autoPicks 永远不写这两个键；
 * 切换预览不发请求，重新出候选后按画法身份保持选中。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  aiDataFigureDiscard,
  aiDataFigurePropose,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataOverview,
  type AiDataProposeResponse,
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
}))
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const discard = vi.mocked(aiDataFigureDiscard)
const understanding = vi.mocked(aiDataUnderstanding)
const st = () => useDataFigureStore.getState()
const ref = (path: string) => ({ path, sheet: null })
const flush = () => new Promise((r) => setTimeout(r, 0))
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const item = (id: string, code: string, extra: Record<string, unknown> = {}) => ({
  id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...extra,
})
const cands = (session: string, codes = ['line_band', 'scatter'], extra: Record<string, unknown> = {}): AiDataProposeResponse =>
  ({
    kind: 'candidates', session, dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: codes.map((c, i) => item(`${session}-${i}`, c, c === 'line_band' ? { error: 'sd' } : {})),
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
    ...extra,
  }) as AiDataProposeResponse

const bodyOf = (n: number) => propose.mock.calls[n]![0]
const ready = (...ids: string[]) => ids.forEach((id) => st().thumbSettled(st().session!, id, true))
const run = async () => {
  st().select(ref('a.csv'))
  await flush()
  await flush()
}

beforeEach(() => {
  resetCommitQueue()
  for (const f of [overview, propose, discard, understanding]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  overview.mockResolvedValue(ov())
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({ openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
})
afterEach(() => {
  useDataFigureStore.getState().clear()
  vi.useRealTimers()
})

describe('主图预览：切换不发请求，重新出候选后按画法身份保持', () => {
  it('selectPreview 只换预览：不调 propose、不放弃会话；图没到的项不可选', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    st().selectPreview('S1-1') // 图还没到
    expect(st().previewKey).toBeNull()
    ready('S1-0', 'S1-1')
    st().selectPreview('S1-1')
    expect(st().previewKey).toContain('scatter')
    expect(propose).toHaveBeenCalledTimes(1)
    expect(discard).not.toHaveBeenCalled()
    expect(st().session).toBe('S1')
  })

  it('换答案重新出候选：id 全换了，选中的仍是同一个画法；那个画法没了就回到第一张（previewKey 解析不到）', async () => {
    propose.mockResolvedValueOnce(cands('S1')).mockResolvedValueOnce(cands('S2')).mockResolvedValueOnce(cands('S3', ['line_band', 'hist']))
    await run()
    ready('S1-0', 'S1-1')
    st().selectPreview('S1-1')
    const key = st().previewKey
    st().setConfirm('error', 'sem')
    await flush()
    expect(st().session).toBe('S2')
    expect(st().previewKey).toBe(key) // 身份串不随 id 变
    const { previewOf } = await import('@/lib/dataFigureAdapter')
    ready('S2-0', 'S2-1')
    expect(previewOf(st().candidates, st().thumbs, st().previewKey)!.id).toBe('S2-1')
    st().setConfirm('error', 'ci95')
    await flush()
    ready('S3-0', 'S3-1')
    expect(previewOf(st().candidates, st().thumbs, st().previewKey)!.id).toBe('S3-0') // scatter 没了
  })

  it('换文件回到第一张', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    ready('S1-0', 'S1-1')
    st().selectPreview('S1-1')
    st().select(ref('b.csv'))
    expect(st().previewKey).toBeNull()
  })
})

describe('setConfirm：只由用户点击写 error / design', () => {
  it('error：写进 answers 重新出候选；选同一个也算确认（照样发）；响应的 error_confirmed 落进状态', async () => {
    propose.mockResolvedValueOnce(cands('S1')).mockResolvedValueOnce(cands('S2', undefined, { error_confirmed: true })).mockResolvedValueOnce(cands('S3', undefined, { error_confirmed: true }))
    await run()
    expect(st().errorConfirmed).toBe(false)
    st().setConfirm('error', 'sd')
    await flush()
    expect(bodyOf(1).answers).toEqual({ error: 'sd' })
    expect(st().errorConfirmed).toBe(true)
    expect(discard).toHaveBeenCalledWith('S1', 'pa')
    st().setConfirm('error', 'sem') // 已确认的可再改
    await flush()
    expect(bodyOf(2).answers).toEqual({ error: 'sem' })
  })

  it('design：不确定 + 从没答过 = 没变，不发请求；选「独立」/「测了多次」才重出；之后再改回不确定会发', async () => {
    propose.mockResolvedValue(cands('S1', undefined, { defaults: [{ question_code: 'pick_design' }] }))
    await run()
    expect(st().designAsked).toBe(true)
    st().setConfirm('design', 'unsure')
    await flush()
    expect(propose).toHaveBeenCalledTimes(1)
    st().setConfirm('design', 'repeated')
    await flush()
    expect(bodyOf(1).answers).toEqual({ design: 'repeated' })
    // 确认之后后端不再问：响应的 defaults 没有 pick_design，但这一行要留着
    expect(st().designAsked).toBe(true)
    st().setConfirm('design', 'unsure')
    await flush()
    expect(bodyOf(2).answers).toEqual({ design: 'unsure' })
  })

  it('不算动了列角色下拉：autoPicks / 建议判据不受影响；和已有的列角色答案并存；出图中不接受', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    useDataFigureStore.setState({ answers: { y: 'z' }, autoPicks: { y: 'z' } })
    st().setConfirm('error', 'sem')
    await flush()
    expect(bodyOf(1).answers).toEqual({ y: 'z', error: 'sem' })
    expect(st().autoPicks).toEqual({ y: 'z' })
    useDataFigureStore.setState({ drawingId: 'S1-0' })
    st().setConfirm('design', 'repeated')
    await flush()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('已在跑的「读懂数据」不因确认重起；确认之后 error / design 仍留在 answers（换列角色不丢）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    understanding.mockResolvedValue({ id: 'U1', state: 'pending' })
    propose.mockResolvedValueOnce(cands('S1', undefined, { understanding: { id: 'U1', state: 'pending' } })).mockResolvedValue(cands('S2', undefined, { understanding: { id: 'U2', state: 'pending' } }))
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    expect(st().understanding).toBe(true)
    st().setConfirm('error', 'sem')
    await vi.advanceTimersByTimeAsync(0)
    expect(st().understanding).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(understanding).toHaveBeenCalledWith('U1', 'pa')
    expect(understanding).not.toHaveBeenCalledWith('U2', expect.anything())
    st().setRole('y', 'z')
    await vi.advanceTimersByTimeAsync(0)
    expect(propose.mock.calls.at(-1)![0].answers).toMatchObject({ error: 'sem', y: 'z' })
  })

  it('模型的建议（读懂数据）自动套用只写 x / y / group / x_measure：就算建议里带了 error / design 也不进 answers 与 autoPicks', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    propose.mockResolvedValueOnce(cands('S1', undefined, { understanding: { id: 'U1', state: 'pending' } })).mockResolvedValue(cands('S2'))
    understanding.mockResolvedValue({ id: 'U1', state: 'ready', answers: { y: 'z', error: 'sem', design: 'repeated' } as never })
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(0)
    const last = propose.mock.calls.at(-1)![0]
    expect(last.answers).toEqual({ y: 'z' })
    expect(st().autoPicks).toEqual({ y: 'z' })
    expect(Object.keys(st().autoPicks)).not.toContain('error')
    expect(Object.keys(st().autoPicks)).not.toContain('design')
  })
})
