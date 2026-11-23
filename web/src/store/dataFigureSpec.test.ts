/**
 * 「从数据到图」草稿自己的规范绑定（`dataFigureStore.specBinding`）：第一次出候选时取自画布、之后草稿自己持有；
 * 出候选请求带 `spec` 只在绑定非空时；换绑定 = 重出候选但不算用户动了下拉；`figure_style` 随响应落进状态；换项目清掉；批量出图同样带 `spec`。
 * 界面一行见 `components/datastudio/dataDraftSpec.test.tsx`，画好后新画布带上绑定见 `lib/dataFigureInherit.test.ts`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataCommitResult,
  type AiDataOverview,
  type AiDataProposeResponse,
} from '@/lib/api'
import { resetCommitQueue } from '@/lib/dataCommitQueue'
import { setCurrentProjectId } from '@/lib/session'
import type { DocumentProfile } from '@/types/document'
import { configureDataBatchHost, useDataBatchStore } from './dataBatchStore'
import { configureDataFigureHost, useDataFigureStore, type DataFigureHost } from './dataFigureStore'

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

const st = () => useDataFigureStore.getState()
const ref = (path: string) => ({ path, sheet: null })
const flush = () => new Promise((r) => setTimeout(r, 0))
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const item = (id: string) => ({ id, title_code: 'line_band', why_code: 'line_band', recommended: id === 'i1', unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const })
const cands = (session: string, extra: Record<string, unknown> = {}): AiDataProposeResponse =>
  ({
    kind: 'candidates', session, dropped: [], items: [item('i1'), item('i2')], render: 'lazy', header_row: 1, defaults: [],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
    mixed_cells: [], ...extra,
  }) as AiDataProposeResponse
const STYLE = { width_single_mm: 85, width_double_mm: 178, font_pt: 7 }

const bindingOf = (id: string): DocumentProfile => ({ id: 'journal-base-v1', journalRef: { id, catalogRev: 1, retrieved: '2026-11-01', label: id } })
/** 带出处的绑定：`journalStyleTargets` 从 `journalSources` 读字号上下限 */
const withFont = (min?: number, max?: number): DocumentProfile => ({
  ...bindingOf('journal-f'),
  journalSources: {
    ...(min != null ? { min_effective_font_size_pt: { value: min } } : {}),
    ...(max != null ? { max_font_size_pt: { value: max } } : {}),
  } as never,
})
const A = bindingOf('journal-a')
const B = bindingOf('journal-b')
const canvasBinding = vi.fn<() => DocumentProfile | null>()
const resolveSpec = vi.fn((b: DocumentProfile): Record<string, unknown> => ({ resolved: b.journalRef?.id ?? b.id }))
const armInherit = vi.fn<NonNullable<DataFigureHost['armInherit']>>()
const stopInherit = vi.fn()
const openScript = vi.fn(async (_script: string, _still: () => boolean) => {})

const host = (over: Partial<DataFigureHost> = {}): DataFigureHost => ({
  openDrawer: () => {}, openScript, projectWritable: () => true, activeCanvasId: () => 'c0', notify: () => {}, dismissAddedNote: () => {},
  canvasBinding, resolveSpec, armInherit, ...over,
})

beforeEach(() => {
  resetCommitQueue()
  for (const f of [files, overview, propose, commit, discard, understanding, canvasBinding, resolveSpec, armInherit, stopInherit, openScript]) f.mockClear()
  propose.mockReset()
  commit.mockReset()
  understanding.mockReset()
  discard.mockResolvedValue({ ok: true })
  overview.mockResolvedValue(ov())
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  canvasBinding.mockReturnValue(A)
  armInherit.mockReturnValue(stopInherit)
  configureDataFigureHost(host())
})
afterEach(() => {
  useDataFigureStore.getState().clear() // 停掉「读懂数据」的轮询定时器，别活过用例
  useDataBatchStore.getState().clear()
  vi.useRealTimers()
})

const run = async (path = 'a.csv') => {
  st().select(ref(path))
  await flush()
  await flush()
}
const bodyOf = (n: number) => propose.mock.calls[n]![0]

describe('草稿的绑定：取一次、之后自己持有', () => {
  it('第一次出候选时从宿主取一次；之后换文件、换个画法都用这份，不再跟画布', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run('a.csv')
    expect(canvasBinding).toHaveBeenCalledTimes(1)
    expect(st()).toMatchObject({ specBinding: A, bindingCaptured: true })
    expect(resolveSpec).toHaveBeenLastCalledWith(A)
    expect(bodyOf(0).spec).toEqual({ resolved: 'journal-a' })
    canvasBinding.mockReturnValue(B) // 画布之后换了期刊：草稿不跟
    await run('b.csv')
    expect(canvasBinding).toHaveBeenCalledTimes(1)
    expect(bodyOf(1).spec).toEqual({ resolved: 'journal-a' })
  })

  it('画布没有绑定 = null：请求里没有 spec 这个键', async () => {
    canvasBinding.mockReturnValue(null)
    propose.mockResolvedValue(cands('S1'))
    await run()
    expect(st()).toMatchObject({ specBinding: null, bindingCaptured: true })
    expect('spec' in bodyOf(0)).toBe(false)
    expect(resolveSpec).not.toHaveBeenCalled()
  })

  it('宿主没注册这几个函数：和从前一样，不带 spec', async () => {
    configureDataFigureHost(host({ canvasBinding: undefined, resolveSpec: undefined, armInherit: undefined }))
    propose.mockResolvedValue(cands('S1'))
    await run()
    expect('spec' in bodyOf(0)).toBe(false)
  })

  it('读表阶段就取了（全是文字的文件没有出候选也能显示真绑定）；换项目 clear 之后重取', async () => {
    overview.mockResolvedValue({ ...ov(), fields: [{ name: 't', kind: 'text', numeric: 0, text: 3, empty: 0, nan: 0, id_like: false }] })
    await run()
    expect(st().phase).toBe('text')
    expect(propose).not.toHaveBeenCalled()
    expect(st().specBinding).toBe(A)
    st().clear()
    expect(st()).toMatchObject({ specBinding: null, bindingCaptured: false, figureStyle: null, specFallback: false })
    canvasBinding.mockReturnValue(B)
    overview.mockResolvedValue(ov())
    propose.mockResolvedValue(cands('S2'))
    await run()
    expect(canvasBinding).toHaveBeenCalledTimes(2)
    expect(bodyOf(0).spec).toEqual({ resolved: 'journal-b' })
  })

  it('关临时标签 / 换文件不清绑定（那是草稿的，不是这份数据的）', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    st().setSpecBinding(B)
    await flush()
    st().closeDraft()
    expect(st().specBinding).toEqual(B)
    await run('b.csv')
    expect(bodyOf(propose.mock.calls.length - 1).spec).toEqual({ resolved: 'journal-b' })
  })
})

describe('font_range：只放期刊自己规定的上下限', () => {
  it('两端都有 / 只有一端 / 都没有 / 绑定为空', async () => {
    propose.mockResolvedValue(cands('S1'))
    canvasBinding.mockReturnValue(withFont(5, 7))
    await run()
    expect(bodyOf(0).font_range).toEqual({ min_pt: 5, max_pt: 7 })
    st().setSpecBinding(withFont(6))
    await flush()
    expect(bodyOf(1).font_range).toEqual({ min_pt: 6 })
    st().setSpecBinding(withFont(undefined, 8))
    await flush()
    expect(bodyOf(2).font_range).toEqual({ max_pt: 8 })
    st().setSpecBinding(A) // 有期刊但没规定字号
    await flush()
    expect('font_range' in bodyOf(3)).toBe(false)
    st().setSpecBinding(null)
    await flush()
    expect('font_range' in bodyOf(4)).toBe(false)
  })
})

describe('响应里的 figure_style / spec_fallback', () => {
  it('候选响应带 figure_style 就存下；下一批没带就清掉', async () => {
    propose.mockResolvedValueOnce(cands('S1', { figure_style: STYLE })).mockResolvedValueOnce(cands('S2'))
    await run()
    expect(st().figureStyle).toEqual(STYLE)
    expect(st().specFallback).toBe(false)
    await run('b.csv')
    expect(st().figureStyle).toBeNull()
  })

  it('规范不合法：spec_fallback 记下、没有 figure_style', async () => {
    propose.mockResolvedValue(cands('S1', { spec_fallback: true }))
    await run()
    expect(st()).toMatchObject({ specFallback: true, figureStyle: null, phase: 'ready' })
  })
})

describe('换绑定 = 重出候选，不算用户动了下拉', () => {
  it('放弃旧会话、带新 spec 重出；answers / autoPicks 原样；旧期刊的数字先清掉', async () => {
    propose.mockResolvedValueOnce(cands('S1', { figure_style: STYLE })).mockResolvedValueOnce(cands('S2', { figure_style: { ...STYLE, width_single_mm: 80 } }))
    await run()
    useDataFigureStore.setState({ answers: { y: 'z' }, autoPicks: { y: 'z' } })
    st().setSpecBinding(B)
    expect(st().figureStyle).toBeNull() // 新名字下面不留旧期刊的数
    expect(discard).toHaveBeenCalledWith('S1', 'pa')
    await flush()
    expect(propose).toHaveBeenCalledTimes(2)
    expect(bodyOf(1).spec).toEqual({ resolved: 'journal-b' })
    expect(bodyOf(1).answers).toEqual({ y: 'z' })
    expect(st()).toMatchObject({ answers: { y: 'z' }, autoPicks: { y: 'z' }, session: 'S2', phase: 'ready' })
    expect(st().figureStyle?.width_single_mm).toBe(80)
  })

  it('清成默认规范（null）：请求里不再带 spec', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    st().setSpecBinding(null)
    await flush()
    expect(propose).toHaveBeenCalledTimes(2)
    expect('spec' in bodyOf(1)).toBe(false)
    expect(st().specBinding).toBeNull()
  })

  it('内容没变不重出；出图中不接受', async () => {
    propose.mockResolvedValue(cands('S1'))
    await run()
    st().setSpecBinding(structuredClone(A))
    await flush()
    expect(propose).toHaveBeenCalledTimes(1)
    useDataFigureStore.setState({ drawingId: 'i1' })
    st().setSpecBinding(B)
    await flush()
    expect(propose).toHaveBeenCalledTimes(1)
    expect(st().specBinding).toEqual(A)
  })

  it('在飞的上一次响应回来也丢掉，只认换绑定之后的那一次', async () => {
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r))).mockResolvedValueOnce(cands('S2', { figure_style: STYLE }))
    st().select(ref('a.csv'))
    await flush()
    expect(st().phase).toBe('loading')
    st().setSpecBinding(B)
    await flush()
    release(cands('OLD', { figure_style: { ...STYLE, font_pt: 1 } }))
    await flush()
    expect(st()).toMatchObject({ session: 'S2', phase: 'ready' })
    expect(st().figureStyle).toEqual(STYLE)
  })

  it('读表阶段换：只存着，出候选时读到的就是新的（不多发一次）', async () => {
    let release!: (v: AiDataOverview) => void
    overview.mockReturnValueOnce(new Promise((r) => (release = r)))
    propose.mockResolvedValue(cands('S1'))
    st().select(ref('a.csv'))
    await flush()
    expect(st().phase).toBe('reading')
    st().setSpecBinding(B)
    release(ov())
    await flush()
    await flush()
    expect(propose).toHaveBeenCalledTimes(1)
    expect(bodyOf(0).spec).toEqual({ resolved: 'journal-b' })
  })

  it('已经在跑的「读懂数据」跟踪不因换规范重起，也不为新响应再起一轮', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    understanding.mockResolvedValue({ id: 'U1', state: 'pending' })
    propose
      .mockResolvedValueOnce(cands('S1', { understanding: { id: 'U1', state: 'pending' } }))
      .mockResolvedValueOnce(cands('S2', { understanding: { id: 'U2', state: 'pending' } }))
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    expect(st().understanding).toBe(true)
    st().setSpecBinding(B)
    await vi.advanceTimersByTimeAsync(0)
    expect(propose).toHaveBeenCalledTimes(2)
    expect(st().understanding).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(understanding).toHaveBeenCalledWith('U1', 'pa')
    expect(understanding).not.toHaveBeenCalledWith('U2', expect.anything())
  })

  it('第一次响应被换绑定取代、没来得及起跟踪：新响应照常起', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    understanding.mockResolvedValue({ id: 'U2', state: 'pending' })
    let release!: (v: AiDataProposeResponse) => void
    propose.mockReturnValueOnce(new Promise((r) => (release = r))).mockResolvedValueOnce(cands('S2', { understanding: { id: 'U2', state: 'pending' } }))
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    st().setSpecBinding(B)
    await vi.advanceTimersByTimeAsync(0)
    release(cands('OLD', { understanding: { id: 'U1', state: 'pending' } }))
    await vi.advanceTimersByTimeAsync(1000)
    expect(understanding).toHaveBeenCalledWith('U2', 'pa')
    expect(understanding).not.toHaveBeenCalledWith('U1', expect.anything())
  })
})

describe('画好之后带绑定去新画布', () => {
  const committed = (): AiDataCommitResult => ({ task: { outcome: 'completed' }, script: 'tavotto_figures/a.py', stem: 'a', data_check: 'verified' })

  it('提交成功、进编辑器之前：用草稿绑定的副本通知宿主；换项目（clear）时收手', async () => {
    propose.mockResolvedValue(cands('S1'))
    commit.mockResolvedValue(committed())
    await run()
    await st().draw('i1')
    expect(armInherit).toHaveBeenCalledTimes(1)
    const [passed] = armInherit.mock.calls[0]!
    expect(passed).toEqual(A)
    expect(passed).not.toBe(st().specBinding) // 副本
    expect(openScript).toHaveBeenCalled()
    st().clear()
    expect(stopInherit).toHaveBeenCalledTimes(1)
  })

  it('用面板里换过的绑定画：带的是换过的那份；绑定为 null 不通知', async () => {
    propose.mockResolvedValue(cands('S1'))
    commit.mockResolvedValue(committed())
    await run()
    st().setSpecBinding(B)
    await flush()
    await st().draw('i1')
    expect(armInherit.mock.calls[0]![0]).toEqual(B)

    armInherit.mockClear()
    st().clear()
    canvasBinding.mockReturnValue(null)
    await run()
    await st().draw('i1')
    expect(armInherit).not.toHaveBeenCalled()
  })
})

describe('批量各画一张：读草稿的绑定（一轮开始时解析一次）', () => {
  const batchCanvas = vi.fn<() => DocumentProfile | null>()
  const batchHost = (withHost = true) =>
    configureDataBatchHost({
      refreshProject: async () => {}, runScript: async () => {}, showAssets: () => {}, showData: () => {}, quietGuideCard: () => {}, projectWritable: () => true,
      ...(withHost ? { canvasBinding: batchCanvas, resolveSpec } : {}),
    })

  beforeEach(() => {
    batchCanvas.mockReset()
    resolveSpec.mockClear()
    useDataBatchStore.getState().clear()
    propose.mockImplementation(async (b) => cands(`S-${b.table}`))
    commit.mockResolvedValue({ task: { outcome: 'completed' }, script: 'tavotto_figures/x.py', stem: 'x', data_check: 'verified' })
  })
  const toggle2 = () => {
    useDataBatchStore.getState().toggle(ref('a.csv'))
    useDataBatchStore.getState().toggle(ref('b.csv'))
  }

  it('草稿取过绑定就用草稿的（不看画布），每份都带同一份 spec，只解析一次', async () => {
    batchHost()
    batchCanvas.mockReturnValue(B)
    useDataFigureStore.setState({ specBinding: A, bindingCaptured: true })
    toggle2()
    await useDataBatchStore.getState().run()
    expect(propose).toHaveBeenCalledTimes(2)
    expect(bodyOf(0).spec).toEqual({ resolved: 'journal-a' })
    expect(bodyOf(1).spec).toEqual({ resolved: 'journal-a' })
    expect(resolveSpec).toHaveBeenCalledTimes(1)
    expect(batchCanvas).not.toHaveBeenCalled()
  })

  it('草稿取过、是 null（默认规范）：不带 spec，也不退回画布', async () => {
    batchHost()
    batchCanvas.mockReturnValue(B)
    useDataFigureStore.setState({ specBinding: null, bindingCaptured: true })
    toggle2()
    await useDataBatchStore.getState().run()
    expect('spec' in bodyOf(0)).toBe(false)
    expect(batchCanvas).not.toHaveBeenCalled()
  })

  it('草稿没取过才退回此刻画布的绑定；宿主没注册则不带', async () => {
    batchHost()
    batchCanvas.mockReturnValue(B)
    toggle2()
    await useDataBatchStore.getState().run()
    expect(bodyOf(0).spec).toEqual({ resolved: 'journal-b' })
    propose.mockClear()
    batchHost(false)
    useDataBatchStore.getState().toggle(ref('c.csv'))
    await useDataBatchStore.getState().run()
    expect('spec' in bodyOf(0)).toBe(false)
  })

  it('期刊规定了字号上下限：每份都带 font_range', async () => {
    batchHost()
    useDataFigureStore.setState({ specBinding: withFont(5, 7), bindingCaptured: true })
    toggle2()
    await useDataBatchStore.getState().run()
    expect(bodyOf(0).font_range).toEqual({ min_pt: 5, max_pt: 7 })
    expect(bodyOf(1).font_range).toEqual({ min_pt: 5, max_pt: 7 })
  })
})
