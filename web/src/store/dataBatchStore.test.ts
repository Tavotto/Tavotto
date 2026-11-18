/**
 * 一次选多份数据、各画一张（`dataBatchStore`）：逐份顺序、串行锁、取第一张候选、各终态、项目代际中止、状态按 tableKey 存。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, aiDataFigureCommit, aiDataFigureDiscard, aiDataFigurePropose, aiDataFilesAll, type AiDataCommitResult, type AiDataProposeResponse } from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { commitPollConfig } from '@/lib/dataCommitPoll'
import { setCurrentProjectId } from '@/lib/session'
import { configureDataBatchHost, useDataBatchStore } from './dataBatchStore'
import { configureDataFigureHost, useDataFigureStore } from './dataFigureStore'

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
const showData = vi.fn()
const quietGuideCard = vi.fn()
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
  for (const f of [propose, commit, discard, files, refresh, showAssets, showData, quietGuideCard, runScript]) f.mockReset()
  runScript.mockResolvedValue(undefined)
  discard.mockResolvedValue({ ok: true })
  refresh.mockResolvedValue(undefined)
  writable = true
  setCurrentProjectId('pa')
  useDataBatchStore.getState().clear()
  useDataFigureStore.getState().clear()
  configureDataBatchHost({ refreshProject: refresh, runScript, showAssets, showData, quietGuideCard, projectWritable: () => writable })
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
    propose.mockClear()
    await st().run('failed')
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

describe('开画后每一行的状态：等着 / 正在画 / 已画 / 没画出来', () => {
  it('开始时这一轮要画的都是「等着」，轮到谁谁变「正在画」，画完变「已画」；没轮到的不留标记', async () => {
    select('a', 'b', 'c')
    const seen: string[] = []
    commit.mockImplementation(async () => {
      seen.push(Object.entries(st().rows).map(([k, v]) => `${JSON.parse(k)[0]}:${v.state}`).join(' '))
      return ok()
    })
    await st().run()
    expect(seen).toEqual(['a:running b:queued c:queued', 'a:done b:running c:queued', 'a:done b:done c:running'])
  })

  it('停下：画完当前这一份就停，后面的不画、仍在选择里、不留「等着」；已画的留着；不切抽屉', async () => {
    select('a', 'b', 'c')
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().run()
    await flush()
    st().stopRun()
    expect(st().stopRequested).toBe(true)
    release(ok())
    await p
    expect(propose).toHaveBeenCalledTimes(1)
    expect(st()).toMatchObject({ running: false, stopRequested: false, pickMode: true })
    expect(Object.fromEntries(Object.entries(st().rows).map(([k, v]) => [JSON.parse(k)[0], v.state]))).toEqual({ a: 'done' })
    expect(st().selection.map((r) => r.path)).toEqual(['b', 'c'])
    expect(showAssets).not.toHaveBeenCalled()
  })

  it('停下点在最后一份上：它画完 = 全部画成，照常切到素材抽屉', async () => {
    select('a', 'b')
    let release!: (v: AiDataCommitResult) => void
    commit.mockResolvedValueOnce(ok()).mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().run()
    while (commit.mock.calls.length < 2) await flush()
    st().stopRun()
    release(ok())
    await p
    expect(showAssets).toHaveBeenCalledTimes(1)
  })

  it('不在进行中时 stopRun 不留下标记（不会让下一轮一开始就停）', async () => {
    select('a', 'b')
    st().stopRun()
    expect(st().stopRequested).toBe(false)
    await st().run()
    expect(commit).toHaveBeenCalledTimes(2)
  })

  it('再试一次（failed）只重做没画成的；停下留下的没轮到的不跟着画，仍选着', async () => {
    select('a', 'b', 'c', 'd')
    let fail!: (e: unknown) => void
    commit.mockResolvedValueOnce(ok()).mockReturnValueOnce(new Promise((_, rej) => (fail = rej)))
    const p = st().run()
    while (commit.mock.calls.length < 2) await flush()
    st().stopRun()
    fail(apiError(500, 'x'))
    await p
    expect(st().selection.map((r) => r.path)).toEqual(['b', 'c', 'd'])
    propose.mockClear()
    commit.mockResolvedValue(ok())
    await st().run('failed')
    expect(propose.mock.calls.map((c) => c[0].table)).toEqual(['b'])
    // 没成的画成了，但 c、d 还选着：不算「全部画成」，留在抽屉、已画的 a b 都在
    expect(showAssets).not.toHaveBeenCalled()
    expect(st().selection.map((r) => r.path)).toEqual(['c', 'd'])
    expect(Object.values(st().rows).map((r) => r.state)).toEqual(['done', 'done'])
  })

  it('新一轮开始时，上一批留下的「已画」标记不再算数（没有没画成的在等重试时）', async () => {
    select('a', 'b')
    let release!: (v: AiDataCommitResult) => void
    commit.mockResolvedValueOnce(ok()).mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = st().run()
    while (commit.mock.calls.length < 2) await flush()
    st().stopRun()
    release(ok())
    await p
    expect(showAssets).toHaveBeenCalledTimes(1) // 两份都画成（停下点在最后一份）
    // 上一批完成后：下一批只看自己的
    select('c', 'd')
    commit.mockResolvedValueOnce(ok()).mockRejectedValueOnce(apiError(500, 'x'))
    await st().run()
    expect(Object.values(st().rows).map((r) => r.state).sort()).toEqual(['done', 'failed'])
  })

  it('取消选一份没画成的 = 把它的「没画出来」一并撤掉', async () => {
    select('a', 'b')
    commit.mockRejectedValue(apiError(500, 'x'))
    await st().run()
    st().toggle(ref('a'))
    expect(st().selection.map((r) => r.path)).toEqual(['b'])
    expect(Object.keys(st().rows).map((k) => JSON.parse(k)[0])).toEqual(['b'])
  })
})

describe('合画一张', () => {
  it('merge：退出多选态并清掉选择，把选中的几份（按选的顺序）交给临时标签；少于 2 份 / 进行中不动', () => {
    select('a')
    st().merge()
    expect(useDataFigureStore.getState().draftOpen).toBe(false)
    select('b', 'c')
    propose.mockResolvedValue({ kind: 'nothing', reasons: [] })
    st().merge()
    expect(st()).toMatchObject({ pickMode: false, selection: [] })
    const f = useDataFigureStore.getState()
    expect(f).toMatchObject({ draftOpen: true, draftActive: true })
    expect(f.group?.map((r) => r.path)).toEqual(['a', 'b', 'c'])
    expect(f.selected).toEqual(ref('a'))
  })

  it('merge：超过 8 份不开（按钮也禁用，这里是第二道）', () => {
    select(...Array.from({ length: 9 }, (_, i) => `f${i}`))
    st().merge()
    expect(useDataFigureStore.getState().draftOpen).toBe(false)
    expect(st().selection).toHaveLength(9)
  })

  it('请求体是 tables（≥2 份，xlsx 带 sheet，其余不带），render 仍是 lazy，没有 table；表头行照带', async () => {
    useDataFigureStore.setState({ headerRows: { 'a.csv': 2 } })
    useDataFigureStore.getState().selectGroup([ref('a.csv'), ref('b.xlsx', 'P1'), ref('b.xlsx', 'P2')])
    propose.mockResolvedValue(cands('G'))
    await flush()
    await flush()
    const body = propose.mock.calls.at(-1)![0]
    expect(body).toEqual({ tables: [{ path: 'a.csv' }, { path: 'b.xlsx', sheet: 'P1' }, { path: 'b.xlsx', sheet: 'P2' }], render: 'lazy', header_rows: { 'a.csv': 2 } })
    expect(body).not.toHaveProperty('table')
  })

  it('少于 2 份不开；单份 select 之后 group 复位；换项目（clear）复位', () => {
    propose.mockResolvedValue({ kind: 'nothing', reasons: [] })
    useDataFigureStore.getState().selectGroup([ref('a')])
    expect(useDataFigureStore.getState().draftOpen).toBe(false)
    useDataFigureStore.getState().selectGroup([ref('a'), ref('b')])
    useDataFigureStore.getState().select(ref('a')) // 同一份第一表，也不能当成「已经开着」而保留组
    expect(useDataFigureStore.getState().group).toBeNull()
    useDataFigureStore.getState().selectGroup([ref('a'), ref('b')])
    useDataFigureStore.getState().clear()
    expect(useDataFigureStore.getState().group).toBeNull()
  })

  it('400 tables_columns_differ / tables_limit_exceeded：各有自己的状态（不是「再试一次」的失败）；别的错照旧', async () => {
    const f = () => useDataFigureStore.getState()
    propose.mockRejectedValueOnce(apiError(400, 'tables_columns_differ'))
    f().selectGroup([ref('a'), ref('b')])
    while (f().phase === 'reading' || f().phase === 'loading') await flush()
    expect(f().phase).toBe('columnsDiffer')
    propose.mockRejectedValueOnce(apiError(400, 'tables_limit_exceeded'))
    f().closeDraft()
    f().selectGroup([ref('a'), ref('b')])
    while (f().phase === 'reading' || f().phase === 'loading') await flush()
    expect(f().phase).toBe('limitExceeded')
    propose.mockRejectedValueOnce(apiError(500, 'x'))
    f().closeDraft()
    f().selectGroup([ref('a'), ref('b')])
    while (f().phase === 'reading' || f().phase === 'loading') await flush()
    expect(f().phase).toBe('failed')
  })

  it('单份流程里同一个码不会走合画的出路', async () => {
    const f = () => useDataFigureStore.getState()
    propose.mockRejectedValueOnce(apiError(400, 'tables_columns_differ'))
    f().select(ref('a'))
    while (f().phase === 'reading' || f().phase === 'loading') await flush()
    expect(f().phase).toBe('failed')
  })

  it('各画一张（合画的出路）：关掉临时标签、恢复这几份的选择、回数据抽屉、直接开始', async () => {
    propose.mockRejectedValueOnce(apiError(400, 'tables_columns_differ'))
    const refs = [ref('a'), ref('b')]
    useDataFigureStore.getState().selectGroup(refs)
    while (useDataFigureStore.getState().phase !== 'columnsDiffer') await flush()
    propose.mockImplementation(async (b) => cands(`S-${b.table}`))
    st().drawEach(useDataFigureStore.getState().group!)
    expect(useDataFigureStore.getState()).toMatchObject({ draftOpen: false, group: null })
    expect(showData).toHaveBeenCalledTimes(1)
    expect(st()).toMatchObject({ pickMode: true, running: true })
    while (st().running) await flush()
    expect(propose.mock.calls.slice(-2).map((c) => c[0].table)).toEqual(['a', 'b'])
    expect(showAssets).toHaveBeenCalledTimes(1)
  })
})

describe('准备卡', () => {
  it('每一轮开画之前先让准备引导卡别自动弹（项目刷新重扫会把我们自己生成的脚本当成新发现）；没有要画的不动它', async () => {
    await st().run()
    expect(quietGuideCard).not.toHaveBeenCalled()
    select('a', 'b')
    await st().run()
    expect(quietGuideCard).toHaveBeenCalledTimes(1)
  })
})

describe('排队的回调轮到时先核项目（Codex P1）', () => {
  /** 占住串行队列：单份流程放一个候选、点一张，它的提交按住不放 */
  const holdQueue = async () => {
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    useDataFigureStore.setState({ selected: ref('z.csv'), phase: 'ready', session: 'SZ', candidates: [{ ...item('z1', true), thumb: '/t/z1' }], thumbs: { z1: 'ready' }, draftOpen: true, draftActive: true })
    const single = useDataFigureStore.getState().draw('z1')
    await flush()
    return { release, single }
  }

  it('批量某一份的 propose / commit 排在被占的队列后面，期间换了项目：轮到时什么都不发', async () => {
    const q = await holdQueue()
    select('a', 'b')
    const p = st().run()
    await flush()
    expect(propose).not.toHaveBeenCalled()
    setCurrentProjectId('pb')
    q.release(ok())
    await p
    await q.single
    expect(propose).not.toHaveBeenCalled()
    expect(commit).toHaveBeenCalledTimes(1) // 只有按住的那一次
    expect(runScript).not.toHaveBeenCalled()
  })

  it('批量「只运行」排在被占的队列后面，期间换了项目：runScript 不被调用（B 里同名脚本不会被误跑）', async () => {
    select('a')
    let releaseRefresh!: () => void
    refresh.mockReturnValueOnce(new Promise<void>((r) => (releaseRefresh = r)))
    const p = st().run()
    while (refresh.mock.calls.length === 0) await flush()
    const q = await holdQueue()
    releaseRefresh() // 批量接着把 runScript 排进队列，排在单份提交后面
    await flush()
    setCurrentProjectId('pb')
    q.release(ok())
    await p
    await q.single
    expect(runScript).not.toHaveBeenCalled()
    expect(showAssets).not.toHaveBeenCalled()
  })

  it('单份流程的提交排在被占的队列后面，期间换了项目：轮到时不发，不拿 A 的会话去 B 提交', async () => {
    select('a')
    let release!: (v: AiDataCommitResult) => void
    commit.mockReturnValueOnce(new Promise((r) => (release = r)))
    const batch = st().run()
    while (commit.mock.calls.length === 0) await flush()
    useDataFigureStore.setState({ selected: ref('z.csv'), phase: 'ready', session: 'SZ', candidates: [{ ...item('z1', true), thumb: '/t/z1' }], thumbs: { z1: 'ready' }, draftOpen: true, draftActive: true })
    const single = useDataFigureStore.getState().draw('z1')
    await flush()
    setCurrentProjectId('pb')
    release(ok())
    await batch
    await single
    expect(commit).toHaveBeenCalledTimes(1)
    expect(commit.mock.calls.some((c) => c[0].session === 'SZ')).toBe(false)
  })
})

describe('不确定的提交结果：重试用同一个请求号（Codex P2）', () => {
  const net = () => new TypeError('Failed to fetch')

  it('commit 抛网络错：这份算没画出来、会话照常放弃（不占后端的 6 个会话名额），只留请求号；重试用同一个请求号重发 commit，不再 propose，回来的结果算画成', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    expect(Object.values(st().rows).map((r) => r.state)).toEqual(['failed'])
    expect(discard).toHaveBeenCalledTimes(1) // 会话已放弃，不为不确定的行占会话
    const first = commit.mock.calls[0]![0]
    propose.mockClear()
    commit.mockResolvedValueOnce(ok())
    await st().run('failed')
    expect(propose).not.toHaveBeenCalled()
    expect(commit).toHaveBeenCalledTimes(2)
    expect(commit.mock.calls[1]![0]).toMatchObject({ session: first.session, id: first.id, client_request_id: first.client_request_id })
    expect(discard).toHaveBeenCalledTimes(1) // 重试成功不再多放弃
    expect(showAssets).toHaveBeenCalledTimes(1) // 全部画成
  })

  it('不确定的失败不试下一张候选（那样会多画一份）', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    expect(commit).toHaveBeenCalledTimes(1)
  })

  it('重试时同一个请求号仍没拿到结果：继续留着，下一次还是这个请求号', async () => {
    select('a')
    commit.mockRejectedValueOnce(net()).mockRejectedValueOnce(net()).mockResolvedValueOnce(ok())
    await st().run()
    await st().run('failed')
    await st().run('failed')
    const rids = commit.mock.calls.map((c) => c[0].client_request_id)
    expect(new Set(rids).size).toBe(1)
    expect(propose).toHaveBeenCalledTimes(1)
  })

  it('后端明确拒绝（有状态码 / 稳定码）：请求号作废，重试重新 propose、换新的请求号', async () => {
    select('a')
    commit.mockRejectedValueOnce(apiError(500, 'x'))
    await st().run()
    const first = commit.mock.calls[0]![0]
    commit.mockResolvedValueOnce(ok())
    await st().run('failed')
    expect(propose).toHaveBeenCalledTimes(2)
    expect(commit.mock.calls[1]![0].client_request_id).not.toBe(first.client_request_id)
  })

  it('重试时同一个请求号被明确拒绝（首次请求没到后端、会话又已放弃 → 404 / 409）：什么都没写过，重新出候选，换新请求号', async () => {
    select('a')
    commit.mockRejectedValueOnce(net()).mockRejectedValueOnce(apiError(409, 'propose_session_expired')).mockResolvedValueOnce(ok())
    await st().run()
    await st().run('failed')
    expect(propose).toHaveBeenCalledTimes(2)
    expect(commit).toHaveBeenCalledTimes(3)
    const [c1, c2, c3] = commit.mock.calls.map((c) => c[0])
    expect(c2!.client_request_id).toBe(c1!.client_request_id)
    expect(c3!.client_request_id).not.toBe(c1!.client_request_id)
    expect(showAssets).toHaveBeenCalledTimes(1)
  })

  it('换项目（clear）：留着的请求号丢掉，不会带到新项目', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    st().clear()
    setCurrentProjectId('pb')
    select('a')
    propose.mockClear()
    await st().run()
    expect(propose).toHaveBeenCalledTimes(1)
  })
})

describe('不确定的行不占后端会话（至多 6 个）', () => {
  it('四份都「结果不确定」：每份的会话都已放弃，没有一个留在后端；四个请求号都留着，重试各自沿用', async () => {
    select('a', 'b', 'c', 'd')
    commit.mockRejectedValue(new TypeError('Failed to fetch'))
    await st().run()
    expect(discard.mock.calls.map((c) => c[0]).sort()).toEqual(['S-a', 'S-b', 'S-c', 'S-d'])
    const first = commit.mock.calls.map((c) => c[0].client_request_id)
    expect(new Set(first).size).toBe(4)
    propose.mockClear()
    commit.mockReset()
    commit.mockResolvedValue(ok())
    await st().run('failed')
    expect(propose).not.toHaveBeenCalled()
    expect(commit.mock.calls.map((c) => c[0].client_request_id)).toEqual(first)
  })
})

describe('同一请求号重发时原任务还在处理中（Codex 第二轮 P2）', () => {
  const net = () => new TypeError('Failed to fetch')
  const running = (): AiDataCommitResult => ({ task: { outcome: null, final: false, deduped: true, task_id: 't1' } })
  beforeEach(() => {
    commitPollConfig.intervalMs = 1
    commitPollConfig.maxMs = 40
  })
  afterEach(() => {
    commitPollConfig.intervalMs = 1500
    commitPollConfig.maxMs = 60_000
  })

  it('批量：去重回「未完成」→ 不 propose、不删请求号、不算失败；之后回 final 成功 → 算画成', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    const first = commit.mock.calls[0]![0]
    propose.mockClear()
    commit.mockResolvedValueOnce(running()).mockResolvedValueOnce(running()).mockResolvedValueOnce(ok())
    await st().run('failed')
    expect(propose).not.toHaveBeenCalled()
    expect(commit).toHaveBeenCalledTimes(4)
    expect(new Set(commit.mock.calls.map((c) => c[0].client_request_id))).toEqual(new Set([first.client_request_id]))
    expect(showAssets).toHaveBeenCalledTimes(1) // 画成
  })

  it('批量：一直未完成到超时 → 这一份失败，但请求号仍保留，下次重试继续问同一个（不换号重画）', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    const first = commit.mock.calls[0]![0]
    propose.mockClear()
    commit.mockResolvedValue(running())
    await st().run('failed')
    expect(Object.values(st().rows).map((r) => r.state)).toEqual(['failed'])
    expect(propose).not.toHaveBeenCalled()
    commit.mockReset()
    commit.mockResolvedValue(ok())
    await st().run('failed')
    expect(propose).not.toHaveBeenCalled()
    expect(commit.mock.calls[0]![0].client_request_id).toBe(first.client_request_id)
    expect(showAssets).toHaveBeenCalledTimes(1)
  })

  it('批量：轮询中途换项目（clear）→ 不再重问', async () => {
    select('a')
    commit.mockRejectedValueOnce(net())
    await st().run()
    commit.mockClear()
    commit.mockResolvedValue(running())
    const p = st().run('failed')
    while (commit.mock.calls.length < 1) await flush()
    st().clear()
    await p
    const n = commit.mock.calls.length
    await new Promise((r) => setTimeout(r, 15))
    expect(commit.mock.calls.length).toBe(n)
  })

  it('单份流程：重试同一请求号时回「未完成」→ 有界地重问同一个，final 后正常进编辑器；超时则这次没成但请求号保留、再点「再试一次」仍是同一个', async () => {
    const f = () => useDataFigureStore.getState()
    const open = vi.fn(async () => {})
    configureDataFigureHost({ openDrawer: () => {}, openScript: open, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
    useDataFigureStore.setState({ selected: ref('z.csv'), phase: 'ready', session: 'SZ', candidates: [{ ...item('z1', true), thumb: '/t/z1' }], thumbs: { z1: 'ready' }, draftOpen: true, draftActive: true })
    commit.mockRejectedValueOnce(net())
    await f().draw('z1')
    expect(f().problem?.kind).toBe('commit')
    const rid = commit.mock.calls[0]![0].client_request_id
    // 超时：仍未完成
    commit.mockResolvedValue(running())
    await f().retry()
    expect(f().problem?.kind).toBe('commit')
    expect(open).not.toHaveBeenCalled()
    // 再点一次：同一个请求号，这次 final
    commit.mockReset()
    commit.mockResolvedValueOnce(running()).mockResolvedValueOnce({ ...ok(), script: 'tavotto_figures/z.py' })
    await f().retry()
    expect(new Set(commit.mock.calls.map((c) => c[0].client_request_id))).toEqual(new Set([rid]))
    expect(open).toHaveBeenCalledWith('tavotto_figures/z.py', expect.any(Function))
  })
})
