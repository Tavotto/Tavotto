/**
 * 一次选多份数据、各画一张（`dataBatchStore`）：逐份顺序、串行锁、取第一张候选、各终态、项目代际中止、状态按 tableKey 存。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, aiDataFigureCommit, aiDataFigureDiscard, aiDataFigurePropose, aiDataFilesAll, type AiDataCommitResult, type AiDataProposeResponse } from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { setCurrentProjectId } from '@/lib/session'
import { configureDataBatchHost, useDataBatchStore } from './dataBatchStore'
import { useDataFigureStore } from './dataFigureStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
const propose = vi.mocked(aiDataFigurePropose)
const commit = vi.mocked(aiDataFigureCommit)
const discard = vi.mocked(aiDataFigureDiscard)
const files = vi.mocked(aiDataFilesAll)
const refresh = vi.fn(async () => {})
const showAssets = vi.fn()
const runScript = vi.fn(async (_s: string) => {})
let writable: boolean | undefined = true

const st = () => useDataBatchStore.getState()
const ref = (path: string, sheet: string | null = null) => ({ path, sheet })
const flush = () => new Promise((r) => setTimeout(r, 0))
const item = (id: string, rec = false) => ({ id, title_code: 'line_band', why_code: 'line_band', recommended: rec, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const })
const cands = (session: string, ids = ['a', 'b']): AiDataProposeResponse => ({ kind: 'candidates', session, dropped: [], render: 'lazy', items: ids.map((id, i) => item(`${session}-${id}`, i === 0)) })
const ok = (): AiDataCommitResult => ({ task: { outcome: 'completed' }, script: 'tavotto_figures/x.py', stem: 'x', data_check: 'verified' })
const apiError = (status: number, code: string) => new ApiError(code, status, { code })
const tables = (...names: string[]) => names.map((n) => ref(n))
const select = (...names: string[]) => names.forEach((n) => st().toggle(ref(n)))

beforeEach(() => {
  resetCommitQueue()
  for (const f of [propose, commit, discard, files, refresh, showAssets, runScript]) f.mockReset()
  runScript.mockResolvedValue(undefined)
  discard.mockResolvedValue({ ok: true })
  refresh.mockResolvedValue(undefined)
  writable = true
  setCurrentProjectId('pa')
  useDataBatchStore.getState().clear()
  useDataFigureStore.getState().clear()
  configureDataBatchHost({ refreshProject: refresh, runScript, showAssets, projectWritable: () => writable })
  propose.mockImplementation(async (b) => cands(`S-${b.table}`))
  commit.mockResolvedValue(ok())
})

describe('选法', () => {
  it('toggle 加进 / 移出；Shift 选一段按可见顺序，起点是最近点的那一行；起点不在可见里就当 toggle', () => {
    const vis = tables('a', 'b', 'c', 'd', 'e')
    st().toggle(ref('b'))
    st().selectRange(ref('d'), vis)
    expect(st().selection.map((r) => r.path)).toEqual(['b', 'c', 'd'])
    st().selectRange(ref('a'), vis) // 起点仍是 b（range 不改起点），反向也行
    expect(new Set(st().selection.map((r) => r.path))).toEqual(new Set(['a', 'b', 'c', 'd']))
    st().toggle(ref('a'))
    expect(st().isSelected(ref('a'))).toBe(false)
    st().setAnchor(ref('zzz'))
    st().selectRange(ref('e'), vis)
    expect(st().isSelected(ref('e'))).toBe(true)
  })

  it('xlsx 的工作表行按 (path, sheet) 区分；clearSelection 清选区，进行中不清', async () => {
    st().toggle(ref('b.xlsx', 'P1'))
    st().toggle(ref('b.xlsx', 'P2'))
    expect(st().selection).toHaveLength(2)
    st().clearSelection()
    expect(st().selection).toHaveLength(0)
    select('a', 'b')
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().run()
    await flush()
    st().clearSelection()
    expect(st().selection).toHaveLength(2) // 进行中不清
    release(ok())
    await p
  })

  it('底部那一条：<2 份又没有要说的就不在；≥2 份、进行中、有没画成的、写不进都在', () => {
    expect(st().barVisible()).toBe(false)
    select('a')
    expect(st().barVisible()).toBe(false)
    select('b')
    expect(st().barVisible()).toBe(true)
  })
})

describe('逐份顺序做', () => {
  it('每份：propose（lazy、不带 answers）→ 提交第一张（推荐）→ 放弃会话 → 刷新项目；不并发、不取缩略图、不打开', async () => {
    select('a.csv', 'b.csv', 'c.csv')
    const log: string[] = []
    let inFlight = 0
    let maxInFlight = 0
    propose.mockImplementation(async (b) => {
      log.push(`propose:${b.table}`)
      return cands(`S-${b.table}`)
    })
    commit.mockImplementation(async (b) => {
      inFlight++
      maxInFlight = Math.max(maxInFlight, inFlight)
      log.push(`commit:${b.session}:${b.id}`)
      await flush()
      inFlight--
      return ok()
    })
    discard.mockImplementation(async (sid) => {
      log.push(`discard:${sid}`)
      return { ok: true }
    })
    refresh.mockImplementation(async () => {
      log.push('refresh')
    })
    runScript.mockImplementation(async () => {
      log.push('run')
    })
    await st().run()
    expect(propose.mock.calls.map((c) => c[0])).toEqual([
      { table: 'a.csv', render: 'lazy' },
      { table: 'b.csv', render: 'lazy' },
      { table: 'c.csv', render: 'lazy' },
    ])
    expect(maxInFlight).toBe(1)
    expect(log).toEqual([
      'propose:a.csv', 'commit:S-a.csv:S-a.csv-a', 'discard:S-a.csv', 'refresh', 'run',
      'propose:b.csv', 'commit:S-b.csv:S-b.csv-a', 'discard:S-b.csv', 'refresh', 'run',
      'propose:c.csv', 'commit:S-c.csv:S-c.csv-a', 'discard:S-c.csv', 'refresh', 'run',
    ])
    expect(commit.mock.calls.every((c) => typeof c[0].client_request_id === 'string')).toBe(true)
  })

  it('进度：正在画第几份 / 共几份', async () => {
    select('a', 'b')
    const seen: number[] = []
    commit.mockImplementation(async () => {
      seen.push(st().progress.done)
      return ok()
    })
    await st().run()
    expect(seen).toEqual([0, 1])
    expect(st().progress).toEqual({ done: 2, total: 2 })
  })

  it('用户改过这份的表头行（单份流程里）：带上 header_rows', async () => {
    useDataFigureStore.setState({ headerRows: { 'a.csv': 3 } })
    select('a.csv', 'b.csv')
    await st().run()
    expect(propose.mock.calls[0]![0]).toMatchObject({ table: 'a.csv', header_rows: { 'a.csv': 3 } })
    expect(propose.mock.calls[1]![0]).not.toHaveProperty('header_rows')
  })
})

describe('多选态 pickMode', () => {
  it('手势（toggle / selectRange）选中即进入；setPickMode(false) / clearSelection 退出并清掉选择；进行中不动', () => {
    expect(st().pickMode).toBe(false)
    st().setPickMode(true)
    expect(st().pickMode).toBe(true)
    st().setPickMode(false)
    expect(st().pickMode).toBe(false)
    select('a')
    expect(st().pickMode).toBe(true)
    st().setPickMode(false)
    expect(st()).toMatchObject({ pickMode: false, selection: [] })
    st().selectRange(ref('c'), tables('a', 'b', 'c'))
    expect(st().pickMode).toBe(true)
    st().clearSelection()
    expect(st().pickMode).toBe(false)
    select('a', 'b')
    useDataBatchStore.setState({ running: true })
    st().setPickMode(false)
    st().clearSelection()
    expect(st()).toMatchObject({ pickMode: true, running: true })
    expect(st().selection).toHaveLength(2)
  })

  it('全部画成 → 退出；部分没画成 → 保持；clear（切项目）复位', async () => {
    select('a', 'b')
    commit.mockResolvedValueOnce(ok()).mockRejectedValueOnce(apiError(500, 'x'))
    await st().run()
    expect(st().pickMode).toBe(true)
    await st().run()
    expect(st().pickMode).toBe(false)
    st().setPickMode(true)
    st().clear()
    expect(st().pickMode).toBe(false)
  })
})

describe('终态', () => {
  it('a) 全部画成：清多选、清进行态、左栏切到素材抽屉，不弹通知', async () => {
    select('a', 'b')
    await st().run()
    expect(st()).toMatchObject({ selection: [], rows: {}, running: false, stop: null })
    expect(showAssets).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('b) 部分没画成：留在数据抽屉；画成的「已画」，没成的「没画出来」并保持选中；再点只重做没成的', async () => {
    select('a', 'b', 'c')
    commit.mockResolvedValueOnce(ok()).mockRejectedValueOnce(apiError(500, 'x')).mockResolvedValueOnce(ok())
    await st().run()
    expect(showAssets).not.toHaveBeenCalled()
    const keys = Object.fromEntries(Object.entries(st().rows).map(([k, v]) => [JSON.parse(k)[0], v.state]))
    expect(keys).toEqual({ a: 'done', b: 'failed', c: 'done' })
    expect(st().selection.map((r) => r.path)).toEqual(['b'])
    expect(st().barVisible()).toBe(true) // 只剩 1 份没成，这一条也在
    propose.mockClear()
    await st().run()
    expect(propose.mock.calls.map((c) => c[0].table)).toEqual(['b']) // 只重做没成的
    expect(showAssets).toHaveBeenCalledTimes(1)
    expect(st().rows).toEqual({})
  })

  it('c) 全部没画成：同 b，都留着', async () => {
    select('a', 'b')
    commit.mockRejectedValue(apiError(500, 'x'))
    await st().run()
    expect(Object.values(st().rows).map((r) => r.state)).toEqual(['failed', 'failed'])
    expect(st().selection).toHaveLength(2)
    expect(showAssets).not.toHaveBeenCalled()
  })

  it('d) 这份没有可画的（nothing）/ 后端还是问了（ask）：算没画成，nothing 的原因句放进 reason', async () => {
    select('a', 'b', 'c')
    propose
      .mockResolvedValueOnce({ kind: 'nothing', reasons: ['no_numeric_column'] })
      .mockResolvedValueOnce({ kind: 'ask', question_code: 'pick_value', options: [], unsure: null })
      .mockResolvedValueOnce(cands('S-c'))
    await st().run()
    const rows = Object.fromEntries(Object.entries(st().rows).map(([k, v]) => [JSON.parse(k)[0], v]))
    expect(rows.a).toMatchObject({ state: 'failed' })
    expect((rows.a as { reason: { key: string } }).reason.key).toMatch(/^dataFigureV2\.(reason\.|nothing\.generic)/)
    expect(rows.b).toMatchObject({ state: 'failed', reason: null })
    expect(rows.c).toMatchObject({ state: 'done' })
    expect(commit).toHaveBeenCalledTimes(1)
  })

  it('e) 写不进文件夹：立刻停整批（后面的不做、仍在多选里），按钮换成「换个文件夹」', async () => {
    select('a', 'b', 'c')
    commit.mockResolvedValueOnce(ok()).mockResolvedValueOnce({ task: { outcome: 'failed' }, reason_code: 'figures_dir_unusable' } as AiDataCommitResult)
    await st().run()
    expect(st().stop).toBe('unwritable')
    expect(propose).toHaveBeenCalledTimes(2)
    expect(st().selection.map((r) => r.path)).toEqual(['b', 'c'])
    expect(showAssets).not.toHaveBeenCalled()
    expect(st().barVisible()).toBe(true)
    // 项目只读（后端没给码）同样
    useDataBatchStore.getState().clear()
    select('a', 'b')
    writable = false
    commit.mockResolvedValueOnce({ task: { outcome: 'failed' } } as AiDataCommitResult)
    await st().run()
    expect(st().stop).toBe('unwritable')
    expect(propose).toHaveBeenCalledTimes(3)
  })

  it('f) 中途换项目 / clear：静默中止，后面的不做、不切抽屉、不留状态；已写出的就是普通脚本', async () => {
    select('a', 'b', 'c')
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().run()
    await flush()
    setCurrentProjectId('pb')
    release(ok())
    await p
    expect(propose).toHaveBeenCalledTimes(1)
    expect(showAssets).not.toHaveBeenCalled()
    // clear（项目重置）同样
    setCurrentProjectId('pa')
    useDataBatchStore.getState().clear()
    select('a', 'b')
    let release2!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release2 = r)))
    const p2 = st().run()
    await flush()
    st().clear()
    release2(ok())
    await p2
    expect(st()).toMatchObject({ selection: [], rows: {}, running: false })
    expect(showAssets).not.toHaveBeenCalled()
  })

  it('g) 脚本落盘了但数据与图对不上：这份算没画成（原因句）；核对没完成 / 没法逐项核对：算画成，不弹任何提示', async () => {
    select('a', 'b', 'c')
    commit
      .mockResolvedValueOnce({ ...ok(), data_check: 'mismatch' })
      .mockResolvedValueOnce({ ...ok(), data_check: 'unknown' })
      .mockResolvedValueOnce({ ...ok(), data_check: undefined })
    await st().run()
    const rows = Object.fromEntries(Object.entries(st().rows).map(([k, v]) => [JSON.parse(k)[0], v]))
    expect(rows.a).toMatchObject({ state: 'failed' })
    expect((rows.a as { reason: { key: string } }).reason.key).toBe('dataFigureV2.result.mismatch')
    expect(rows.b).toMatchObject({ state: 'done' })
    expect(rows.c).toMatchObject({ state: 'done' })
  })

  it('第一张提交时后端说它不在了（404 propose_candidate_unknown）：试下一张；都不行才算没画成', async () => {
    select('a', 'b')
    commit.mockRejectedValueOnce(apiError(404, 'propose_candidate_unknown')).mockResolvedValueOnce(ok()).mockRejectedValue(apiError(404, 'propose_candidate_unknown'))
    await st().run()
    expect(commit.mock.calls.slice(0, 2).map((c) => c[0].id)).toEqual(['S-a-a', 'S-a-b'])
    const rows = Object.fromEntries(Object.entries(st().rows).map(([k, v]) => [JSON.parse(k)[0], v.state]))
    expect(rows).toEqual({ a: 'done', b: 'failed' })
  })

  it('会话过期：静默重新出一次再提交', async () => {
    select('a', 'b')
    commit.mockRejectedValueOnce(apiError(409, 'propose_session_expired'))
    await st().run()
    expect(propose.mock.calls.map((c) => c[0].table)).toEqual(['a', 'a', 'b'])
    expect(st().rows).toEqual({})
  })

  it('h) 清单重读不丢状态：状态按 tableKey 存在这个 store 里，不放在行对象上', async () => {
    select('a', 'b')
    commit.mockResolvedValueOnce(ok()).mockRejectedValueOnce(apiError(500, 'x'))
    await st().run()
    const before = JSON.stringify(st().rows)
    files.mockResolvedValue({ files: [{ path: 'a', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 1, rows: 3 } }], truncated: false })
    await useDataFigureStore.getState().loadFiles()
    useDataFigureStore.getState().invalidateFiles?.()
    await flush()
    expect(JSON.stringify(st().rows)).toBe(before)
  })
})

describe('和单份流程共用一把串行锁', () => {
  it('批量的提交在飞时，单份点一张的提交排在后面，任何时刻只有一个提交', async () => {
    select('a', 'b')
    let inFlight = 0
    let maxInFlight = 0
    const releases: (() => void)[] = []
    commit.mockImplementation(
      () =>
        new Promise<AiDataCommitResult>((resolve) => {
          inFlight++
          maxInFlight = Math.max(maxInFlight, inFlight)
          releases.push(() => {
            inFlight--
            resolve(ok())
          })
        }),
    )
    const batch = st().run()
    await flush()
    expect(commit).toHaveBeenCalledTimes(1)
    // 单份流程：直接放一个候选让它点
    useDataFigureStore.setState({
      selected: ref('z.csv'),
      phase: 'ready',
      session: 'SZ',
      candidates: [{ ...item('z1', true), thumb: '/t/z1' }],
      thumbs: { z1: 'ready' },
      draftOpen: true,
      draftActive: true,
    })
    const single = useDataFigureStore.getState().draw('z1')
    await flush()
    expect(commit).toHaveBeenCalledTimes(1) // 单份在排队
    releases[0]!() // 批量的 a 画完：队列里单份的排在批量 b 前面（b 要先 propose 再排队）
    for (let i = 1; i < 3; i++) {
      while (releases.length <= i) await flush()
      expect(inFlight).toBe(1)
      releases[i]!()
      await flush()
    }
    await Promise.all([batch, single])
    expect(maxInFlight).toBe(1)
    expect(commit).toHaveBeenCalledTimes(3)
  })
})

describe('画完只运行、不打开（素材卡才有预览）', () => {
  it('每份提交成功后按顺序运行一次它的新脚本，运行排在串行锁里不并发；运行失败不算没画出来、不加提示', async () => {
    select('a', 'b')
    commit.mockResolvedValueOnce({ ...ok(), script: 'tavotto_figures/a.py' }).mockResolvedValueOnce({ ...ok(), script: 'tavotto_figures/b.py' })
    let inFlight = 0
    let maxInFlight = 0
    runScript.mockImplementationOnce(async () => {
      inFlight++
      maxInFlight = Math.max(maxInFlight, inFlight)
      await flush()
      inFlight--
      throw new Error('boom')
    })
    await st().run()
    expect(runScript.mock.calls.map((c) => c[0])).toEqual(['tavotto_figures/a.py', 'tavotto_figures/b.py'])
    expect(maxInFlight).toBe(1)
    expect(st().rows).toEqual({}) // 全部算画成 → 清空并切到素材抽屉
    expect(showAssets).toHaveBeenCalledTimes(1)
  })

  it('没落盘 / 对不上的不运行；运行期间这一行仍是「正在画」，运行完才是「已画」', async () => {
    select('a', 'b')
    commit.mockResolvedValueOnce({ ...ok(), data_check: 'mismatch' }).mockResolvedValueOnce(ok())
    let release!: () => void
    runScript.mockReturnValueOnce(new Promise<void>((r) => (release = r)))
    const p = st().run()
    while (runScript.mock.calls.length === 0) await flush()
    const states = Object.values(st().rows).map((r) => r.state)
    expect(states).toEqual(['failed', 'running'])
    release()
    await p
    expect(runScript).toHaveBeenCalledTimes(1)
  })
})
