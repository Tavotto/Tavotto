/**
 * 数据页「按表记住」：切走时把已完整的草稿留下（后端会话不放弃），切回同一张表 / 同一组表 / 同一份期刊绑定直接还原，
 * 不读表、不出候选、缩略图不重画；半成品不存；文件大小变了 / 超 TTL / 换项目 / 超上限就作废并放弃会话；
 * 还原出来的草稿缩略图取不到、或提交回 stale 码，走现有的静默重出。TTL 用可注入的时钟。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataFileEntry,
  type AiDataOverview,
  type AiDataProposeResponse,
} from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { setCurrentProjectId } from '@/lib/session'
import type { DocumentProfile } from '@/types/document'
import { KEEP_MAX, KEEP_TTL_MS, configureDataFigureHost, setKeepClock, useDataFigureStore } from './dataFigureStore'

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
const commit = vi.mocked(aiDataFigureCommit)
const discard = vi.mocked(aiDataFigureDiscard)
const understanding = vi.mocked(aiDataUnderstanding)
const openScript = vi.fn(async (_s: string, _still: () => boolean) => {})

const st = () => useDataFigureStore.getState()
const ref = (path: string) => ({ path, sheet: null })
const flush = () => new Promise((r) => setTimeout(r, 0))
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const entry = (path: string, size: number): AiDataFileEntry => ({ path, size, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } })
let sizes: Record<string, number>
const fileList = () => ({ files: Object.entries(sizes).map(([p, n]) => entry(p, n)), truncated: false })
const sessionOf = (key: string) => `S-${key}`
const item = (id: string, code: string) => ({ id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const })
const respFor = (key: string, extra: Record<string, unknown> = {}): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: sessionOf(key), dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: [item(`${key}-0`, 'line_band'), item(`${key}-1`, 'scatter')],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
    ...extra,
  }) as AiDataProposeResponse
const keyOfBody = (b: { table?: string; tables?: { path: string }[] }) => (b.tables ? b.tables.map((t) => t.path).join('+') : b.table!)

/** 这张表的缩略图都到了（= 草稿完整，可以留） */
const settle = () => st().candidates.forEach((c) => st().thumbSettled(st().session!, c.id, true))
const open = async (path: string, done = true) => {
  st().select(ref(path))
  await flush()
  await flush()
  if (done) settle()
}
const openGroup = async (paths: string[]) => {
  st().selectGroup(paths.map(ref))
  await flush()
  await flush()
  settle()
}
const proposeCalls = () => propose.mock.calls.length
const discarded = () => discard.mock.calls.map((c) => c[0])

const BIND_X: DocumentProfile = { id: 'journal-base-v1', journalRef: { id: 'x', catalogRev: 1, retrieved: '2026-11-01', label: 'X' } }
const BIND_Y: DocumentProfile = { id: 'journal-base-v1', journalRef: { id: 'y', catalogRev: 1, retrieved: '2026-11-01', label: 'Y' } }
let binding: DocumentProfile | null

beforeEach(() => {
  resetCommitQueue()
  for (const f of [files, overview, propose, commit, discard, understanding, openScript]) f.mockReset()
  discard.mockResolvedValue({ ok: true })
  overview.mockResolvedValue(ov())
  sizes = { 'a.csv': 10, 'b.csv': 20, 'c.csv': 30 }
  files.mockImplementation(async () => fileList())
  propose.mockImplementation(async (b) => respFor(keyOfBody(b as never)))
  binding = null
  let now = 1_000_000
  setKeepClock(() => now)
  ;(globalThis as { __tick?: (ms: number) => void }).__tick = (ms) => {
    now += ms
  }
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({
    openDrawer: () => {}, openScript, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {},
    canvasBinding: () => binding,
    resolveSpec: (b) => ({ resolved: b.journalRef?.id ?? b.id }),
  })
})
afterEach(() => {
  useDataFigureStore.getState().clear()
  vi.useRealTimers()
  setKeepClock(() => Date.now())
})
const tick = (ms: number) => (globalThis as unknown as { __tick: (ms: number) => void }).__tick(ms)

describe('来回切：切回直接还原', () => {
  it('A→B→A：不读表、不出候选、不放弃 A 的会话，状态一模一样；再 →B 也一样（A→B→A→B 来回几轮不新增请求）', async () => {
    await st().loadFiles()
    await open('a.csv')
    st().selectPreview('a.csv-1')
    useDataFigureStore.setState({ answers: { y: 'z' }, autoPicks: { y: 'z' } })
    const before = st()
    const keep = { candidates: before.candidates, thumbs: before.thumbs, roles: before.roles, previewKey: before.previewKey, answers: before.answers, autoPicks: before.autoPicks, session: before.session }
    await open('b.csv')
    expect(proposeCalls()).toBe(2)
    expect(discarded()).not.toContain(sessionOf('a.csv')) // 留着，不放弃
    overview.mockClear()
    st().select(ref('a.csv'))
    await flush()
    expect(proposeCalls()).toBe(2)
    expect(overview).not.toHaveBeenCalled()
    expect(st()).toMatchObject({ selected: ref('a.csv'), phase: 'ready', draftOpen: true, draftActive: true, drawingId: null, ...keep })
    // 再回 B、再回 A、再回 B：B 也是还原出来的
    st().select(ref('b.csv'))
    await flush()
    expect(st().session).toBe(sessionOf('b.csv'))
    st().select(ref('a.csv'))
    st().select(ref('b.csv'))
    expect(proposeCalls()).toBe(2)
    expect(discard).not.toHaveBeenCalled()
  })

  it('合画的一组表也能记住；顺序不同是另一组', async () => {
    await st().loadFiles()
    await openGroup(['a.csv', 'b.csv'])
    await open('c.csv')
    const n = proposeCalls()
    st().selectGroup([ref('a.csv'), ref('b.csv')])
    expect(proposeCalls()).toBe(n)
    expect(st()).toMatchObject({ group: [ref('a.csv'), ref('b.csv')], session: sessionOf('a.csv+b.csv'), phase: 'ready' })
    await open('c.csv')
    st().selectGroup([ref('b.csv'), ref('a.csv')])
    await flush()
    expect(proposeCalls()).toBe(n + 1) // 反序 = 没留过 = 重新出
  })

  it('表头行换了：键不同，不还原', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    useDataFigureStore.setState({ headerRows: { 'a.csv': 3 } })
    const n = proposeCalls()
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
  })
})

describe('半成品不存', () => {
  it('缩略图还有没到的：切走就和从前一样丢并放弃会话，切回来重新出', async () => {
    await st().loadFiles()
    await open('a.csv', false) // 缩略图都还在 pending
    await open('b.csv')
    expect(discarded()).toContain(sessionOf('a.csv'))
    const n = proposeCalls()
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
  })

  it('还在读 / 出候选：不存，旧响应回来也不落到别的表上', async () => {
    await st().loadFiles()
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r)))
    st().select(ref('a.csv'))
    await flush()
    expect(st().phase).toBe('loading')
    await open('b.csv')
    release(respFor('a.csv'))
    await flush()
    expect(st().selected).toEqual(ref('b.csv'))
    expect(st().session).toBe(sessionOf('b.csv'))
    const n = proposeCalls()
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1) // A 没留过
  })

  it('正在出图（drawingId）：select 不动，什么都不存', async () => {
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ drawingId: 'a.csv-0' })
    st().select(ref('b.csv'))
    await flush()
    expect(proposeCalls()).toBe(1)
    expect(st().selected).toEqual(ref('a.csv'))
  })
})

describe('什么时候不能用旧的', () => {
  it('文件大小变了：清单重读后那条作废并放弃会话；切回来重新出', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    sizes['a.csv'] = 11
    await st().loadFiles()
    expect(discarded()).toContain(sessionOf('a.csv'))
    const n = proposeCalls()
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
  })

  it('清单还没重读、还原那一刻也比一次大小', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    // 清单直接被换成新的（没经过 loadFiles 的清理）
    useDataFigureStore.setState({ files: { status: 'ready', entries: [entry('a.csv', 99), entry('b.csv', 20)], truncated: false } })
    const n = proposeCalls()
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
    expect(discarded()).toContain(sessionOf('a.csv'))
  })

  it('已知缺口：大小没变但内容变了比不出来 → 还原出旧图，提交时后端回 propose_data_changed 才静默重出', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    st().select(ref('a.csv')) // 清单大小不变：照常还原
    expect(st().session).toBe(sessionOf('a.csv'))
    propose.mockImplementation(async (b) => respFor(`${keyOfBody(b as never)}-new`))
    commit.mockRejectedValueOnce(new ApiError('propose_data_changed', 409, { code: 'propose_data_changed' })).mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/a.py', stem: 'a', data_check: 'verified' })
    await st().draw('a.csv-0')
    await flush()
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ table: 'a.csv' }) // 静默重出了
    expect(openScript).toHaveBeenCalledWith('tavotto_figures/a.py', expect.any(Function))
  })

  it('超过 TTL 的条目直接丢（时钟可注入）；没到的还在', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    tick(KEEP_TTL_MS - 1)
    st().select(ref('a.csv'))
    expect(st().session).toBe(sessionOf('a.csv')) // 还没过
    await open('b.csv') // B 是还原的（B 的 liveAt 还是更早的 0 点）：刚好也没过
    tick(2)
    const n = proposeCalls()
    st().select(ref('a.csv')) // 现在 A 的 liveAt 距今 > TTL
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
    expect(discarded()).toContain(sessionOf('a.csv'))
  })

  it('换项目（clear）：全部丢并按各自的项目放弃会话', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    st().clear()
    expect(discard).toHaveBeenCalledWith(sessionOf('a.csv'), 'pa')
    setCurrentProjectId('pb')
    await st().loadFiles()
    const n = proposeCalls()
    await open('a.csv')
    expect(proposeCalls()).toBe(n + 1)
  })

  it('上限 KEEP_MAX：超了丢最久没用的并放弃它的会话；其余仍能还原', async () => {
    expect(KEEP_MAX).toBe(5)
    sizes = Object.fromEntries(Array.from({ length: 8 }, (_, i) => [`t${i}.csv`, i + 1]))
    await st().loadFiles()
    for (let i = 0; i < 7; i++) await open(`t${i}.csv`) // 留了 t0..t5 共 6 条 → 丢 t0
    expect(discarded()).toEqual([sessionOf('t0.csv')])
    const n = proposeCalls()
    st().select(ref('t1.csv'))
    expect(proposeCalls()).toBe(n)
    expect(st().session).toBe(sessionOf('t1.csv'))
    st().select(ref('t0.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
  })

  it('期刊绑定不一样：不命中（旧条目留着，绑定换回来仍能还原）；切表时绑定是沿用的（项目内取一次，各表共用）', async () => {
    binding = BIND_X
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    expect(st().specBinding).toEqual(BIND_X) // 切到 B 沿用同一份
    st().setSpecBinding(BIND_Y)
    await flush()
    settle()
    const n = proposeCalls()
    st().select(ref('a.csv')) // A 是按 X 存的，当前是 Y
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
    expect(propose.mock.calls.at(-1)![0].spec).toEqual({ resolved: 'y' })
  })
})

describe('还原之后会话其实没了', () => {
  it('缩略图取不到：静默带着现有答案重出一次（不卡在破图上），不循环', async () => {
    await st().loadFiles()
    await open('a.csv')
    useDataFigureStore.setState({ answers: { y: 'z' } })
    await open('b.csv')
    st().select(ref('a.csv'))
    const n = proposeCalls()
    st().thumbSettled(sessionOf('a.csv'), 'a.csv-0', false) // 图片 404
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 1)
    expect(propose.mock.calls.at(-1)![0]).toMatchObject({ table: 'a.csv', answers: { y: 'z' } })
    expect(st()).toMatchObject({ phase: 'ready', session: sessionOf('a.csv') })
    st().thumbSettled(sessionOf('a.csv'), 'a.csv-1', false) // 新会话的图：走原来的「去掉这张」，不再重出
    expect(proposeCalls()).toBe(n + 1)
  })

  it('提交回 propose_session_expired：对还原出来的草稿同样静默重出并接着画', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    st().select(ref('a.csv'))
    commit.mockRejectedValueOnce(new ApiError('propose_session_expired', 404, { code: 'propose_session_expired' })).mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/a.py', stem: 'a', data_check: 'verified' })
    const n = proposeCalls()
    await st().draw('a.csv-1')
    await flush()
    expect(proposeCalls()).toBe(n + 1)
    expect(commit).toHaveBeenCalledTimes(2)
    expect(openScript).toHaveBeenCalled()
  })
})

describe('其它', () => {
  it('提交成功变成正式的图后不再存；它的留存条目也没有', async () => {
    await st().loadFiles()
    await open('a.csv')
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/a.py', stem: 'a', data_check: 'verified' })
    await st().draw('a.csv-0')
    await flush()
    expect(st().draftOpen).toBe(false)
    const n = proposeCalls()
    await open('b.csv')
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(proposeCalls()).toBe(n + 2) // b 一次、a 重出一次：a 没被存
  })

  it('closeDraft 丢当前并放弃它的会话；别的表的留存还在', async () => {
    await st().loadFiles()
    await open('a.csv')
    await open('b.csv')
    st().closeDraft()
    expect(discarded()).toEqual([sessionOf('b.csv')])
    const n = proposeCalls()
    st().select(ref('a.csv'))
    expect(proposeCalls()).toBe(n)
    expect(st().session).toBe(sessionOf('a.csv'))
  })

  it('切走时「读懂数据」还在跑：还原后接着轮询同一个任务', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await st().loadFiles()
    understanding.mockResolvedValue({ id: 'U1', state: 'pending' })
    propose.mockImplementation(async (b) => respFor(keyOfBody(b as never), keyOfBody(b as never) === 'a.csv' ? { understanding: { id: 'U1', state: 'pending' } } : {}))
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    settle()
    expect(st().understanding).toBe(true)
    st().select(ref('b.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    settle()
    expect(st().understanding).toBe(false)
    st().select(ref('a.csv'))
    expect(st().understanding).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(understanding).toHaveBeenCalledWith('U1', 'pa')
  })
})
