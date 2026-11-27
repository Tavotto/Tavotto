/**
 * 换误差类型要多开一个后端候选会话（至多 6 个，超了后端悄悄删最老的）：先把「按表记住」的留存丢到只剩 `KEEP_MAX - 1` 个、
 * 最旧的先丢并放弃它的会话；用户开着的这份草稿（当前会话）一个字不动。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { aiDataFigureDiscard, aiDataFigurePropose, aiDataFilesAll, aiDataOverview, type AiDataFileEntry, type AiDataOverview, type AiDataProposeResponse } from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { setCurrentProjectId } from '@/lib/session'
import { KEEP_MAX, configureDataFigureHost, reserveBackendSession, useDataFigureStore } from './dataFigureStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  aiDataUnderstanding: vi.fn(),
}))
const discard = vi.mocked(aiDataFigureDiscard)
const st = () => useDataFigureStore.getState()
const flush = () => new Promise((r) => setTimeout(r, 0))
const paths = Array.from({ length: KEEP_MAX + 1 }, (_, i) => `t${i}.csv`)
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const entry = (path: string): AiDataFileEntry => ({ path, size: 10, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } })
const resp = (key: string): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: `S-${key}`, dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: [{ id: `${key}-0`, title_code: 'line_band', why_code: 'w', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t', thumbnail_status: 'pending' }],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: [] } },
  }) as AiDataProposeResponse

beforeEach(() => {
  resetCommitQueue()
  for (const f of [discard, vi.mocked(aiDataFilesAll), vi.mocked(aiDataOverview), vi.mocked(aiDataFigurePropose)]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  vi.mocked(aiDataOverview).mockResolvedValue(ov())
  vi.mocked(aiDataFilesAll).mockResolvedValue({ files: paths.map(entry), truncated: false })
  vi.mocked(aiDataFigurePropose).mockImplementation(async (b) => resp((b as { table: string }).table))
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({ openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
})
afterEach(() => useDataFigureStore.getState().clear())

describe('reserveBackendSession', () => {
  it('留存满了：只丢最旧的一个并放弃它的会话；当前草稿不动', async () => {
    await st().loadFiles()
    for (const p of paths) {
      st().select({ path: p, sheet: null })
      await flush()
      await flush()
      st().candidates.forEach((c) => st().thumbSettled(st().session!, c.id, true))
    }
    // 最后一张是当前草稿，前面 KEEP_MAX 张留着
    const current = st().session
    expect(current).toBe(`S-${paths.at(-1)}`)
    discard.mockClear()
    reserveBackendSession()
    expect(discard.mock.calls.map((c) => c[0])).toEqual([`S-${paths[0]}`])
    expect(st().session).toBe(current)
    expect(st().phase).toBe('ready')
    // 再调一次：已经腾出来了，不再丢
    discard.mockClear()
    reserveBackendSession()
    expect(discard).not.toHaveBeenCalled()
  })

  it('留存没满：什么都不丢', async () => {
    await st().loadFiles()
    st().select({ path: paths[0]!, sheet: null })
    await flush()
    await flush()
    discard.mockClear()
    reserveBackendSession()
    expect(discard).not.toHaveBeenCalled()
  })
})
