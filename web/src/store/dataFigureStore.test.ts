/**
 * 数据页的 store（ADR 0178）：项目代际 / 请求序号 / 发起时的项目、选择题的回答累积、各结局（verified / unverifiable /
 * unplotted / mismatch / 缺 data_check）、静默重新出备选、提交请求号的复用。
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
import { useDataFigureStore } from './dataFigureStore'

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

const st = () => useDataFigureStore.getState()
const apiError = (status: number, code: string) => new ApiError(code, status, { code })
const ref = (path: string, sheet: string | null = null) => ({ path, sheet })
const ov = (over: Partial<AiDataOverview> = {}): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [], preview_rows: [], truncated: false, lead_lines: [], raw_lines: [], ...over,
})
const item = (id: string, title: string, recommended = false) => ({
  id, title_code: title, why_code: title, recommended, unplotted: { count: 0, code: null }, thumbnail_url: `/api/ai/data-figure/thumbnail/S1/${id}`,
})
const cands = (session = 'S1', ids: [string, string][] = [['i1', 'line_band'], ['i2', 'scatter']]): AiDataProposeResponse => ({
  kind: 'candidates', session, dropped: [], items: ids.map(([id, t], k) => item(id, t, k === 0)),
})
const committed = (extra: Partial<AiDataCommitResult> = {}): AiDataCommitResult => ({
  task: { outcome: 'completed' }, script: 'tavotto_figures/a_figure.py', stem: 'a_figure', data_check: 'verified', unplotted: { count: 0, rows: [], code: null }, ...extra,
})
const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  // 用例里排的 *Once 回应不能漏进下一个用例：mockReset（clear 不清 once 队列）
  for (const f of [files, overview, propose, commit, discard, refresh, prepOpen]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useDataFigureStore.setState({ overlay: true })
  useProjectStore.setState({ project: { open: true, writable: true } })
  useProjectPreparationStore.setState({ open: prepOpen })
  overview.mockResolvedValue(ov())
})

describe('清单与概览：项目代际、请求序号、发起时的项目', () => {
  it('清单请求带发起那一刻的项目；换项目（clear）之后回来的结果作废，不落进新项目', async () => {
    let release!: (v: Awaited<ReturnType<typeof aiDataFilesAll>>) => void
    files.mockReturnValue(new Promise((r) => (release = r)))
    const p = st().loadFiles()
    expect(files).toHaveBeenCalledWith('pa')
    setCurrentProjectId('pb')
    useDataFigureStore.getState().clear()
    release({ files: [{ path: 'old.csv', size: 1 }], truncated: false })
    await p
    expect(st().files).toEqual({ status: 'idle' })
  })

  it('清单：后到的旧响应不盖新的；失败不清空已有的清单', async () => {
    files.mockResolvedValueOnce({ files: [{ path: 'a.csv', size: 1 }], truncated: false })
    await st().loadFiles()
    expect(st().files).toMatchObject({ status: 'ready' })
    files.mockRejectedValueOnce(apiError(500, 'x'))
    await st().loadFiles()
    expect(st().files).toMatchObject({ status: 'error', entries: [{ path: 'a.csv' }] })

    let slow!: (v: Awaited<ReturnType<typeof aiDataFilesAll>>) => void
    files.mockReturnValueOnce(new Promise((r) => (slow = r)))
    const first = st().loadFiles()
    files.mockResolvedValueOnce({ files: [{ path: 'new.csv', size: 1 }], truncated: false })
    await st().loadFiles()
    slow({ files: [{ path: 'stale.csv', size: 1 }], truncated: false })
    await first
    expect(st().files).toMatchObject({ status: 'ready', entries: [{ path: 'new.csv' }] })
  })

  it('点一张表：读它的概览（带项目、路径与工作表）；同一张表不重复请求', async () => {
    st().select(ref('w.xlsx', 'S 1'))
    await flush()
    expect(overview).toHaveBeenCalledWith({ path: 'w.xlsx', sheet: 'S 1', headerRow: null }, 'pa')
    st().select(ref('w.xlsx', 'S 1'))
    await flush()
    expect(overview).toHaveBeenCalledTimes(1)
  })

  it('概览在飞时换了项目：回来的不落地', async () => {
    let release!: (v: AiDataOverview) => void
    overview.mockReturnValue(new Promise((r) => (release = r)))
    st().select(ref('a.csv'))
    useDataFigureStore.getState().clear()
    release(ov())
    await flush()
    expect(st().overviews).toEqual({})
  })

  it('点一行当作「每列的名字」：按那一行重读；之后出图带上 header_rows', async () => {
    st().select(ref('t.txt'))
    await flush()
    st().setHeaderRow('t.txt', null, 4)
    await flush()
    expect(overview).toHaveBeenLastCalledWith({ path: 't.txt', sheet: null, headerRow: 4 }, 'pa')
    propose.mockResolvedValue({ kind: 'nothing', reasons: ['no_usable_table'] })
    await st().start()
    expect(propose.mock.calls[0]![0]).toMatchObject({ table: 't.txt', header_rows: { 't.txt': 4 } })
  })
})

describe('出备选：选择题、混了文字、选画法、没有备选', () => {
  beforeEach(() => st().select(ref('a.csv')))

  it('start：带选中的表（xlsx 带工作表）；返回 ask → 回答并进下一次的 answers（后端给的 answer 原样）', async () => {
    st().select(ref('w.xlsx', 'S'))
    propose.mockResolvedValueOnce({
      kind: 'ask', question_code: 'pick_value', unsure: null,
      options: [{ code: 'column', params: { column: 'y' }, answer: { y: 'y' } }],
    })
    await st().start()
    expect(propose.mock.calls[0]![0]).toEqual({ table: 'w.xlsx', sheet: 'S' })
    expect(propose.mock.calls[0]![1]).toBe('pa')
    expect(st().scene).toMatchObject({ kind: 'ask', code: 'pick_value' })
    propose.mockResolvedValueOnce(cands())
    await st().answer({ code: 'column', params: {}, answer: { y: 'y' } })
    expect(propose.mock.calls[1]![0]).toMatchObject({ table: 'w.xlsx', sheet: 'S', answers: { y: 'y' } })
    expect(st().scene).toMatchObject({ kind: 'pick', selectedId: 'i1' })
  })

  it('混了文字：一次问多列，两列的回答都并进 answers.cells', async () => {
    const col = (c: string) => ({ column: c, non_numeric: 2, rows: 10, preview: [], more: 0, options: [] })
    propose.mockResolvedValueOnce({ kind: 'ask', question_code: 'mixed_cells', columns: [col('c1'), col('c2')] })
    await st().start()
    expect(st().scene).toMatchObject({ kind: 'mixed' })
    propose.mockResolvedValueOnce(cands())
    await st().answerCells([
      { column: 'c1', option: { code: 'skip', params: {}, answer: { cells: { c1: { mode: 'skip' } } } } },
      { column: 'c2', option: { code: 'extract', params: {}, answer: { cells: { c2: { mode: 'extract' } } } } },
    ])
    expect(propose.mock.calls[1]![0]).toMatchObject({ answers: { cells: { c1: { mode: 'skip' }, c2: { mode: 'extract' } } } })
  })

  it('候选：默认选推荐的；缩略图地址带发起时的项目；选另一张', async () => {
    propose.mockResolvedValueOnce(cands())
    await st().start()
    const sc = st().scene
    if (sc.kind !== 'pick') throw new Error('not pick')
    expect(sc.selectedId).toBe('i1')
    expect(sc.items[0]!.thumb).toBe('/api/ai/data-figure/thumbnail/S1/i1?pj=pa')
    st().choose('i2')
    expect(st().scene).toMatchObject({ kind: 'pick', selectedId: 'i2' })
    st().choose('nope')
    expect(st().scene).toMatchObject({ selectedId: 'i2' })
  })

  it('nothing：原因码原样放进状态；空备选 / 形状不对按失败，不当成拿到了空的', async () => {
    propose.mockResolvedValueOnce({ kind: 'nothing', reasons: ['no_numeric_column'] })
    await st().start()
    expect(st().scene).toEqual({ kind: 'nothing', reasons: ['no_numeric_column'] })
    propose.mockResolvedValueOnce({ kind: 'candidates', session: 'S', items: [], dropped: [] })
    await st().start()
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'propose' } })
  })

  it('出备选失败：一句话 + 能再试（retry 重发同样的请求）', async () => {
    propose.mockRejectedValueOnce(apiError(400, 'data_not_found'))
    await st().start()
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'propose' } })
    propose.mockResolvedValueOnce(cands())
    await st().retry()
    expect(st().scene.kind).toBe('pick')
  })

  it('出备选在飞时换了一张表：旧的回来不盖新的屏；换项目同理', async () => {
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().start()
    st().select(ref('b.csv'))
    release(cands())
    await p
    expect(st().scene).toEqual({ kind: 'browse' })

    let release2!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release2 = r)))
    const p2 = st().start()
    useDataFigureStore.getState().clear()
    release2(cands())
    await p2
    expect(st().scene).toEqual({ kind: 'browse' })
  })

  it('返回：放弃这一轮的候选会话（discard），回到浏览', async () => {
    propose.mockResolvedValueOnce(cands('SX'))
    await st().start()
    st().back()
    expect(discard).toHaveBeenCalledWith('SX', 'pa')
    expect(st().scene).toEqual({ kind: 'browse' })
  })
})

describe('画这张：各结局', () => {
  beforeEach(async () => {
    st().select(ref('a.csv'))
    propose.mockResolvedValue(cands())
    await st().start()
  })

  it('verified：先刷新项目，再走「打开并运行」那条现有路径进编辑器；数据页收起', async () => {
    commit.mockResolvedValueOnce(committed())
    await st().draw()
    expect(commit.mock.calls[0]![0]).toMatchObject({ session: 'S1', id: 'i1' })
    expect(typeof commit.mock.calls[0]![0].client_request_id).toBe('string')
    expect(refresh).toHaveBeenCalled()
    expect(prepOpen).toHaveBeenCalledTimes(1)
    expect(prepOpen.mock.calls[0]![0]).toMatchObject({ script: 'tavotto_figures/a_figure.py' })
    expect(prepOpen.mock.calls[0]![1]).toMatchObject({ run: true })
    expect(st().overlay).toBe(false)
  })

  it('unverifiable：不直接跳，先说「没法逐项核对」，点打开才进编辑器；绝不写成已核对', async () => {
    commit.mockResolvedValueOnce(committed({ data_check: 'unverifiable' }))
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'drawn', notes: { check: 'unverifiable', unplotted: null } })
    expect(prepOpen).not.toHaveBeenCalled()
    await st().openDrawn()
    expect(prepOpen).toHaveBeenCalledTimes(1)
  })

  it('缺 data_check（去重的回应）/ unknown：按没核对过说，不当成 verified', async () => {
    commit.mockResolvedValueOnce(committed({ data_check: undefined }))
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'drawn', notes: { check: 'unknown' } })
  })

  it('有几行没画出来：告诉用户是哪些行', async () => {
    commit.mockResolvedValueOnce(
      committed({ unplotted: { count: 2, code: 'rows_not_plotted', rows: [{ row: 7, column: 'x', reason: 'x_not_a_value' }, { row: 9, column: 'x', reason: 'x_not_a_value' }] } }),
    )
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'drawn', notes: { unplotted: { count: 2, rows: [7, 9] }, check: null } })
  })

  it('mismatch：说清并留在数据页（脚本已落盘，不跳进编辑器）', async () => {
    commit.mockResolvedValueOnce(committed({ data_check: 'mismatch', can_delete_script: true, task: { outcome: 'failed' } }))
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'mismatch', script: 'tavotto_figures/a_figure.py' } })
    expect(prepOpen).not.toHaveBeenCalled()
  })

  it('没落盘、项目文件夹写不进：给「换个文件夹」，不是只会再试', async () => {
    useProjectStore.setState({ project: { open: true, writable: false } })
    commit.mockResolvedValueOnce({ task: { outcome: 'failed', error: 'executor_error:PermissionError' } })
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'unwritable' } })
  })

  it('没落盘、别的原因：一句话 + 再试一次', async () => {
    commit.mockResolvedValueOnce({ task: { outcome: 'failed' }, reason_code: 'candidate_render_failed' })
    await st().draw()
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'commit', retryable: true } })
  })

  it('正在画时再点「画这张」不会重复提交', async () => {
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().draw()
    await st().draw()
    expect(commit).toHaveBeenCalledTimes(1)
    expect(st().scene.kind).toBe('drawing')
    release(committed({ data_check: 'unverifiable' }))
    await p
  })

  it('正在画时点清单里别的表：不理（画完再换），在飞的提交不会没人认领', async () => {
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().draw()
    st().select(ref('other.csv'))
    expect(st().selected).toEqual(ref('a.csv'))
    release(committed({ data_check: 'unverifiable' }))
    await p
  })

  it('提交在飞时换了项目：回来的结果作废，不进编辑器', async () => {
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().draw()
    useDataFigureStore.getState().clear()
    release(committed())
    await p
    expect(prepOpen).not.toHaveBeenCalled()
    expect(st().scene).toEqual({ kind: 'browse' })
  })

  it('没收到结果（断网）：保留请求号，再试一次复用它（不会画出第二份）；后端明确拒绝则换号', async () => {
    commit.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await st().draw()
    // 浏览器自己的英文报错不摆给用户（只有标题那一句）；后端明确说过的才显示原因
    expect(st().scene).toMatchObject({ kind: 'problem', problem: { kind: 'commit', error: null } })
    const rid = commit.mock.calls[0]![0].client_request_id
    commit.mockRejectedValueOnce(apiError(409, 'data_figure_recipe_invalid'))
    await st().retry()
    expect(commit.mock.calls[1]![0].client_request_id).toBe(rid)
    // 后端明确拒绝过：号作废，「再试一次」从出备选重来（旧的备选 / 请求号都不再沿用）
    propose.mockResolvedValueOnce(cands('S3'))
    await st().retry()
    expect(st().scene).toMatchObject({ kind: 'pick', session: 'S3' })
    commit.mockResolvedValueOnce(committed({ data_check: 'unverifiable' }))
    await st().draw()
    expect(commit.mock.calls[2]![0].client_request_id).not.toBe(rid)
  })

  it('会话过期 / 数据变了：静默重新出备选，按推荐的画法对号接着画；不弹错', async () => {
    commit.mockRejectedValueOnce(apiError(404, 'propose_session_expired'))
    propose.mockResolvedValueOnce(cands('S2', [['n1', 'line_band'], ['n2', 'scatter']]))
    commit.mockResolvedValueOnce(committed())
    await st().draw()
    expect(propose).toHaveBeenCalledTimes(2)
    expect(commit.mock.calls[1]![0]).toMatchObject({ session: 'S2', id: 'n1' })
    expect(commit.mock.calls[1]![0].client_request_id).not.toBe(commit.mock.calls[0]![0].client_request_id)
    expect(prepOpen).toHaveBeenCalledTimes(1)
  })

  it('静默重试只一次：再过期就让用户看新的备选（不无限循环）；选的那种没了也看新的', async () => {
    st().choose('i2')
    commit.mockRejectedValueOnce(apiError(409, 'propose_data_changed'))
    propose.mockResolvedValueOnce(cands('S2', [['n1', 'line_band'], ['n3', 'hist']]))
    await st().draw()
    expect(commit).toHaveBeenCalledTimes(1)
    expect(st().scene).toMatchObject({ kind: 'pick', session: 'S2' })
  })
})

describe('抽屉 + 浮层：打开 / 关闭 / 换项目', () => {
  it('select 打开浮层；closeOverlay 关掉并放弃这一轮的备选；正在画时关不掉', async () => {
    st().select(ref('a.csv'))
    expect(st().overlay).toBe(true)
    propose.mockResolvedValueOnce(cands('SX'))
    await st().start()
    st().closeOverlay()
    expect(st()).toMatchObject({ overlay: false, scene: { kind: 'browse' } })
    expect(discard).toHaveBeenCalledWith('SX', 'pa')
    st().select(ref('a.csv'))
    propose.mockResolvedValueOnce(cands())
    await st().start()
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().draw()
    st().closeOverlay()
    expect(st().overlay).toBe(true)
    release(committed({ data_check: 'unverifiable' }))
    await p
  })

  it('enter：开「数据」抽屉；只有一个可读文件就直接开它的浮层，多个只开抽屉', async () => {
    const drawer = vi.fn()
    const { configureDataFigureHost } = await import('./dataFigureStore')
    configureDataFigureHost({ openDrawer: drawer, openScript: async () => {}, projectWritable: () => true })
    const ok = (path: string) => ({ path, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows: 3 } })
    files.mockResolvedValue({ files: [ok('one.csv'), { path: 'bad.dat', size: 1, table: { status: 'unreadable', code: 'data_binary', delimiter: null, header_row: null, columns: null, rows: null, preview: [] } }], truncated: false })
    st().enter()
    await flush()
    await flush()
    expect(drawer).toHaveBeenCalled()
    expect(st()).toMatchObject({ overlay: true, selected: { path: 'one.csv', sheet: null } })
    files.mockResolvedValue({ files: [ok('a.csv'), ok('b.csv')], truncated: false })
    st().enter()
    await flush()
    await flush()
    expect(st()).toMatchObject({ overlay: false, selected: null })
    await import('@/lib/dataFigureHost') // 还原真宿主给后面的用例
  })

  it('clear 回到初始（浮层关、选中清掉）', () => {
    st().select(ref('a.csv'))
    useDataFigureStore.getState().clear()
    expect(st()).toMatchObject({ overlay: false, selected: null, scene: { kind: 'browse' }, answers: {}, headerRows: {} })
  })
})
