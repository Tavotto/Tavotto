/**
 * 数据出图的宿主（`lib/dataFigureHost.ts`）：出图成功后「刷新 → 打开并运行」这条链上，刷新挂起期间用户切了项目，就整个放弃——
 * A 项目的相对脚本路径不能拿到 B 项目里去跑（项目隔离不变量，web/AGENTS.md）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { aiDataFigureCommit, aiDataFigurePropose, aiDataOverview, type AiDataCommitResult } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import '@/lib/dataFigureHost'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(async () => ({ files: [], truncated: false })),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
const refresh = vi.hoisted(() => vi.fn<() => Promise<void>>())
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: refresh }))

const flush = () => new Promise((r) => setTimeout(r, 0))
const open = vi.fn(async (..._a: unknown[]) => {})

const drawOne = async () => {
  vi.mocked(aiDataOverview).mockResolvedValue({
    path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 1, layout: null,
    fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }], preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
  })
  vi.mocked(aiDataFigurePropose).mockResolvedValue({
    kind: 'candidates', session: 'S1', dropped: [], render: 'lazy',
    items: [{ id: 'i1', title_code: 'line_band', why_code: 'line_band', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', thumbnail_status: 'pending' }],
  })
  vi.mocked(aiDataFigureCommit).mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/a.py', stem: 'a', data_check: 'verified' } as AiDataCommitResult)
  useDataFigureStore.getState().select({ path: 'a.csv', sheet: null })
  await flush()
  await flush()
  return useDataFigureStore.getState().draw('i1')
}

beforeEach(() => {
  for (const f of [refresh, open, vi.mocked(aiDataFigureCommit), vi.mocked(aiDataFigurePropose), vi.mocked(aiDataOverview)]) f.mockReset()
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useProjectStore.setState({ project: { open: true, name: 'A', writable: true } })
  useProjectPreparationStore.setState({ open })
})

describe('出图后打开新图：刷新挂起期间换项目', () => {
  it('切到别的项目（项目标识变了）：不调 open、不留 fresh', async () => {
    let release!: () => void
    refresh.mockReturnValue(new Promise<void>((r) => (release = r)))
    const p = drawOne()
    await flush()
    await flush()
    expect(refresh).toHaveBeenCalled()
    setCurrentProjectId('pb') // 用户切到了项目 B
    release()
    await p
    await flush()
    expect(open).not.toHaveBeenCalled()
  })

  it('store 换代（clear，项目重置）：同样不调 open', async () => {
    let release!: () => void
    refresh.mockReturnValue(new Promise<void>((r) => (release = r)))
    const p = drawOne()
    await flush()
    await flush()
    useDataFigureStore.getState().clear()
    release()
    await p
    await flush()
    expect(open).not.toHaveBeenCalled()
  })

  it('没换项目：照常刷新后打开', async () => {
    refresh.mockResolvedValue(undefined)
    await drawOne()
    await flush()
    expect(open).toHaveBeenCalledTimes(1)
  })
})
