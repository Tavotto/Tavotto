/**
 * 数据页的 store（ADR 0178）：临时标签的开 / 关 / 复用、项目代际 / 请求序号 / 发起时的项目、出图的各结局（verified / unverifiable /
 * unplotted / mismatch / 缺 data_check）、静默重新出备选、提交请求号的复用。界面走一遍在 `components/datastudio/dataDraft.test.tsx`。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  type AiDataCommitResult,
  type AiDataOverview,
  type AiDataProposeResponse,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useProjectStore } from '@/store/projectStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import '@/lib/dataFigureHost' // 宿主：刷新 + 「打开并运行」+ 项目可写性
import { configureDataFigureHost, useDataFigureStore } from './dataFigureStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
const refresh = vi.hoisted(() => vi.fn(async () => {}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: refresh }))

const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const commit = vi.mocked(aiDataFigureCommit)
const discard = vi.mocked(aiDataFigureDiscard)
const prepOpen = vi.fn(async (..._a: unknown[]) => {})
const notify = vi.fn()

const st = () => useDataFigureStore.getState()
const apiError = (status: number, code: string) => new ApiError(code, status, { code })
const ref = (path: string, sheet: string | null = null) => ({ path, sheet })
const ov = (over: Partial<AiDataOverview> = {}): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [], preview_rows: [], truncated: false, lead_lines: [], raw_lines: [], ...over,
})
const item = (id: string, title: string, recommended = false) => ({
  id, title_code: title, why_code: title, recommended, unplotted: { count: 0, code: null }, thumbnail_url: `/api/ai/data-figure/thumbnail/S1/${id}`, thumbnail_status: 'pending' as const,
})
const cands = (session = 'S1', ids: [string, string][] = [['i1', 'line_band'], ['i2', 'scatter']]): AiDataProposeResponse => ({
  kind: 'candidates', session, dropped: [], items: ids.map(([id, t], k) => item(id, t, k === 0)), render: 'lazy',
  header_row: 1, defaults: [],
  roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: ['g'] } },
  mixed_cells: [],
})
const committed = (extra: Partial<AiDataCommitResult> = {}): AiDataCommitResult => ({
  task: { outcome: 'completed' }, script: 'tavotto_figures/a_figure.py', stem: 'a_figure', data_check: 'verified', unplotted: { count: 0, rows: [], code: null }, ...extra,
})
const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  for (const f of [files, overview, propose, commit, discard, refresh, prepOpen, notify]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useProjectStore.setState({ project: { open: true, writable: true } })
  useProjectPreparationStore.setState({ open: prepOpen })
  overview.mockResolvedValue(ov())
  // 宿主：刷新 + 「打开并运行」+ 项目可写性（个别用例会换掉它，这里每次还原）
  configureDataFigureHost({
    openDrawer: () => {},
    openScript: async (script) => {
      await refresh()
      await prepOpen(script)
    },
    projectWritable: () => useProjectStore.getState().project?.writable,
    activeCanvasId: () => null,
    notify,
    dismissAddedNote: () => {},
  })
})

const numeric = [{ name: 'x', kind: 'numeric' as const, numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }]
const withNumbers = () => overview.mockResolvedValue(ov({ fields: numeric, preview_rows: [{ line: 2, cells: ['1'] }] }))
const run = async (r = ref('a.csv')) => {
  st().select(r)
  await flush()
  await flush()
}

describe('临时标签：开 / 复用 / 关', () => {
  it('select 开临时标签并立刻出候选；再点别的文件复用同一个标签、放弃上一份的候选', async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1')).mockResolvedValueOnce(cands('S2'))
    await run(ref('a.csv'))
    expect(st()).toMatchObject({ draftOpen: true, draftActive: true, phase: 'ready', session: 'S1' })
    await run(ref('b.csv'))
    expect(discard).toHaveBeenCalledWith('S1', 'pa')
    expect(st()).toMatchObject({ draftOpen: true, selected: ref('b.csv'), session: 'S2' })
  })

  it('closeDraft 关标签并放弃候选；切到真画布只是不激活（deactivate），标签还在', async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1'))
    await run()
    st().deactivateDraft()
    expect(st()).toMatchObject({ draftOpen: true, draftActive: false })
    st().activateDraft()
    st().closeDraft()
    expect(discard).toHaveBeenCalledWith('S1', 'pa')
    expect(st()).toMatchObject({ draftOpen: false, draftActive: false, selected: null, phase: 'idle', candidates: [] })
  })

  it('换项目（clear）：在飞的候选请求回来也丢掉，A 项目的结果不落进 B', async () => {
    withNumbers()
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r)))
    st().select(ref('a.csv'))
    await flush()
    st().clear()
    release(cands('SX'))
    await flush()
    expect(st()).toMatchObject({ draftOpen: false, phase: 'idle', session: null })
  })

  it('请求用发起那一刻的项目：中途换项目后缩略图地址仍带 A 的标识', async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1'))
    await run()
    expect(st().candidates[0]!.thumb).toContain('pj=pa')
  })

  it('enter：开抽屉；只有一个可读文件就直接开它的临时标签，多个只开抽屉', async () => {
    withNumbers()
    const drawer = vi.fn()
    configureDataFigureHost({ openDrawer: drawer, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify, dismissAddedNote: () => {} })
    const okf = (path: string) => ({ path, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows: 3 } })
    files.mockResolvedValue({ files: [okf('one.csv'), { path: 'bad.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null, preview: [] } }], truncated: false })
    propose.mockResolvedValue(cands('S1'))
    st().enter()
    await flush()
    await flush()
    expect(drawer).toHaveBeenCalled()
    expect(st()).toMatchObject({ draftOpen: true, selected: ref('one.csv') })
    st().clear()
    files.mockResolvedValue({ files: [okf('a.csv'), okf('b.csv')], truncated: false })
    st().enter()
    await flush()
    await flush()
    expect(st().draftOpen).toBe(false)
  })
})

describe('出图的各结局', () => {
  const ready = async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1'))
    await run()
  }
  it('verified：直接进编辑器，临时标签收掉，记下 fresh', async () => {
    await ready()
    commit.mockResolvedValueOnce(committed())
    await st().draw('i1')
    expect(refresh).toHaveBeenCalled()
    expect(prepOpen).toHaveBeenCalled()
    expect(st()).toMatchObject({ draftOpen: false, drawingId: null, fresh: { file: ref('a.csv') } })
  })
  it('有行没画出来 / 没法逐项核对：照样打开图，一句话走浮动通知，不留在临时标签里；缺 data_check 不写成已核对', async () => {
    await ready()
    commit.mockResolvedValueOnce(committed({ data_check: 'verified', unplotted: { count: 3, rows: [{ row: 3, column: 'x', reason: 'x' }], code: 'rows_not_plotted' } }))
    await st().draw('i1')
    expect(prepOpen).toHaveBeenCalled()
    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify.mock.calls[0]![0]).toMatchObject({ key: 'dataFigureV2.note.unplotted', values: { count: 3 } })
    expect(st().draftOpen).toBe(false)
  })
  it('缺 data_check = 没核对过：通知说「核对没能完成」，绝不静默当成已核对', async () => {
    await ready()
    commit.mockResolvedValueOnce(committed({ data_check: undefined }))
    await st().draw('i1')
    expect(notify.mock.calls.at(-1)![0]).toMatchObject({ key: 'dataFigureV2.result.unknown' })
    expect(prepOpen).toHaveBeenCalled()
  })
  it('数据对不上 / 写不进：各一种结局；没落盘 = 没画出来', async () => {
    await ready()
    commit.mockResolvedValueOnce(committed({ data_check: 'mismatch' }))
    await st().draw('i1')
    expect(st().problem).toMatchObject({ kind: 'mismatch', script: 'tavotto_figures/a_figure.py' })
    st().dismissProblem()
    useProjectStore.setState({ project: { open: true, writable: false } })
    commit.mockResolvedValueOnce({ task: { outcome: 'failed' } } as AiDataCommitResult)
    await st().draw('i1')
    expect(st().problem).toMatchObject({ kind: 'unwritable' })
  })
  it('同一次点击的重试复用请求号（没收到结果的断网），不会画出第二份', async () => {
    await ready()
    commit.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(committed())
    await st().draw('i1')
    expect(st().problem).toMatchObject({ kind: 'commit' })
    await st().retry()
    expect(commit.mock.calls[1]![0].client_request_id).toBe(commit.mock.calls[0]![0].client_request_id)
  })
  it('备选过期：静默重新出候选，原来选的画法还在就接着画', async () => {
    await ready()
    commit.mockRejectedValueOnce(apiError(409, 'propose_session_expired')).mockResolvedValueOnce(committed())
    propose.mockResolvedValueOnce(cands('S2', [['j1', 'scatter'], ['j2', 'line_band']]))
    await st().draw('i1')
    expect(commit.mock.calls[1]![0]).toMatchObject({ session: 'S2', id: 'j2' })
    expect(st().problem).toBeNull()
  })
  it('正在画时再点别的文件：不换表，只把标签翻出来；不重复提交', async () => {
    await ready()
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().draw('i1')
    void st().draw('i2')
    st().closeDraft()
    st().select(ref('b.csv'))
    expect(st()).toMatchObject({ draftOpen: true, selected: ref('a.csv'), drawingId: 'i1' })
    expect(commit).toHaveBeenCalledTimes(1)
    release(committed())
    await p
  })
  it('redraw：回到这份数据的临时标签并重新出候选', async () => {
    await ready()
    commit.mockResolvedValueOnce(committed())
    await st().draw('i1')
    propose.mockResolvedValueOnce(cands('S3'))
    st().redraw()
    await flush()
    await flush()
    expect(st()).toMatchObject({ draftOpen: true, selected: ref('a.csv'), fresh: null, session: 'S3' })
  })
})

describe('懒模式：不再自动答题，右栏改动带着 answers 重来', () => {
  it('请求带 render=lazy；响应里的列角色 / 表头行 / 非数字格进状态；每张缩略图各自 pending', async () => {
    withNumbers()
    propose.mockResolvedValueOnce({ ...(cands('S1') as Extract<AiDataProposeResponse, { kind: 'candidates' }>), mixed_cells: [{ column: 'v', non_numeric: 2, rows: 10, mode: 'skip' }, { column: 'w', non_numeric: 1, rows: 10, mode: 'skip' }] })
    await run()
    expect(propose.mock.calls[0]![0]).toMatchObject({ table: 'a.csv', render: 'lazy' })
    expect(propose.mock.calls[0]![0]).not.toHaveProperty('answers')
    expect(st()).toMatchObject({ phase: 'ready', headerRowUsed: 1, roles: { x: 'x', y: 'y', group: null }, thumbs: { i1: 'pending', i2: 'pending' } })
    expect(st().mixed).toMatchObject({ mode: 'skip', columns: [{ column: 'v', nonNumeric: 2 }, { column: 'w', nonNumeric: 1 }] })
  })

  it('取不到图（422）的那张静默去掉；一张都没画出来走「没画出来」', async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1'))
    await run()
    st().thumbSettled('S1', 'i1', true)
    st().thumbSettled('S1', 'i2', false)
    expect(st().thumbs).toEqual({ i1: 'ready', i2: 'failed' })
    expect(st().phase).toBe('ready')
    st().thumbSettled('S0', 'i1', false) // 别的会话的回执不算
    propose.mockResolvedValueOnce(cands('S2'))
    st().setRole('y', 'z')
    await flush()
    st().thumbSettled('S2', 'i1', false)
    st().thumbSettled('S2', 'i2', false)
    expect(st().phase).toBe('failed')
  })

  it('换纵轴 / 横轴 / 分组：answers 带上、重新出候选；重新出的期间右栏的角色不清', async () => {
    withNumbers()
    propose.mockResolvedValueOnce(cands('S1'))
    await run()
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r)))
    st().setRole('y', 'z')
    expect(st()).toMatchObject({ phase: 'loading', roles: { y: 'y' }, candidates: [] })
    expect(discard).toHaveBeenCalledWith('S1', 'pa')
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { y: 'z' }, render: 'lazy' })
    release(cands('S2'))
    await flush()
    propose.mockResolvedValueOnce(cands('S3'))
    st().setRole('group', 'none')
    expect(propose.mock.calls[2]![0]).toMatchObject({ answers: { y: 'z', group: 'none' } })
    await flush()
  })

  it('非数字格改成取数字：对每一列都带上 mode', async () => {
    withNumbers()
    const base = cands('S1') as Extract<AiDataProposeResponse, { kind: 'candidates' }>
    propose.mockResolvedValueOnce({ ...base, mixed_cells: [{ column: 'v', non_numeric: 2, rows: 10, mode: 'skip' }, { column: 'w', non_numeric: 1, rows: 10, mode: 'skip' }] })
    await run()
    propose.mockResolvedValueOnce({ ...cands('S2') as Extract<AiDataProposeResponse, { kind: 'candidates' }>, mixed_cells: [{ column: 'v', non_numeric: 2, rows: 10, mode: 'extract' }, { column: 'w', non_numeric: 1, rows: 10, mode: 'extract' }] })
    st().setMixedMode('extract')
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { cells: { v: { mode: 'extract' }, w: { mode: 'extract' } } } })
    await flush()
    expect(st().mixed?.mode).toBe('extract')
  })

  it('后端万一还是问了（ask）：不替用户答，按失败处理', async () => {
    withNumbers()
    propose.mockResolvedValueOnce({ kind: 'ask', question_code: 'pick_value', options: [], unsure: null })
    await run()
    expect(st().phase).toBe('failed')
  })
})

describe('单位行与横轴列夹文字', () => {
  it('响应的 units_row 进状态；缺失按 false', async () => {
    withNumbers()
    propose.mockResolvedValueOnce({ ...(cands('S1') as Extract<AiDataProposeResponse, { kind: 'candidates' }>), units_row: true })
    await run()
    expect(st().unitsRow).toBe(true)
    propose.mockResolvedValueOnce(cands('S2'))
    st().setRole('y', 'z')
    await flush()
    expect(st().unitsRow).toBe(false)
  })

  it('横轴列夹 2 格文字：右栏 mixed 正常；切「只用里面的数字」后仍取不出数，界面不出错，画出来后说「有 2 行没画出来」', async () => {
    withNumbers()
    const m = (mode: 'skip' | 'extract') => [{ column: 'day', non_numeric: 2, rows: 18, mode }]
    const base = (sess: string, mode: 'skip' | 'extract') => ({ ...(cands(sess) as Extract<AiDataProposeResponse, { kind: 'candidates' }>), mixed_cells: m(mode) })
    propose.mockResolvedValueOnce(base('S1', 'skip')).mockResolvedValueOnce(base('S2', 'extract'))
    await run()
    expect(st().mixed).toMatchObject({ mode: 'skip', columns: [{ column: 'day', nonNumeric: 2 }] })
    st().setMixedMode('extract')
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { cells: { day: { mode: 'extract' } } } })
    await flush()
    expect(st()).toMatchObject({ phase: 'ready', mixed: { mode: 'extract' } })
    commit.mockResolvedValueOnce(committed({ unplotted: { count: 2, rows: [{ row: 3, column: 'day', reason: 'x' }, { row: 4, column: 'day', reason: 'x' }], code: 'rows_not_plotted' } }))
    await st().draw('i1')
    expect(notify.mock.calls.at(-1)![0]).toMatchObject({ key: 'dataFigureV2.note.unplotted', values: { count: 2 } })
    expect(prepOpen).toHaveBeenCalled()
  })

  it('切到「只用里面的数字」之后后端不再列出这些列：右栏那一行留着、能切回去', async () => {
    withNumbers()
    const base = cands('S1') as Extract<AiDataProposeResponse, { kind: 'candidates' }>
    propose.mockResolvedValueOnce({ ...base, mixed_cells: [{ column: 'day', non_numeric: 2, rows: 18, mode: 'skip' }] }).mockResolvedValueOnce({ ...cands('S2') as Extract<AiDataProposeResponse, { kind: 'candidates' }>, mixed_cells: [] })
    await run()
    st().setMixedMode('extract')
    await flush()
    expect(st().mixed).toMatchObject({ mode: 'extract', columns: [{ column: 'day', nonNumeric: 2 }] })
    propose.mockResolvedValueOnce({ ...cands('S3') as Extract<AiDataProposeResponse, { kind: 'candidates' }>, mixed_cells: [] })
    st().setMixedMode('skip')
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ answers: { cells: { day: { mode: 'skip' } } } })
    await flush()
    expect(st().mixed).toMatchObject({ mode: 'skip' })
    // 换了文件就清掉
    propose.mockResolvedValueOnce(cands('S4'))
    await run(ref('b.csv'))
    expect(st().mixed).toBeNull()
  })
})
