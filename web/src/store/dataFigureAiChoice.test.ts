/**
 * 「读懂数据」用哪个 AI（`understandingChoice`）的 store 侧：整个对象随清单 / 轻量 GET / 用户的选择回来，`understandingAvailable` 由它派生，
 * 旧后端（没有这个对象）退回老字段；选择：关了停掉在途的读懂数据且列角色原样、换了在用的连接对当前这份重跑一次（拦「动过下拉 / 出图中」）、
 * 失败抛出且状态不变；请求序号只认最新一次；留存的草稿把 AI 选择放进命中判断。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  aiDataChoiceGet,
  aiDataChoiceSet,
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiDataOverview,
  type AiDataProposeResponse,
  type AiDataUnderstandingChoice,
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
  aiDataChoiceGet: vi.fn(),
  aiDataChoiceSet: vi.fn(),
}))
const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const understanding = vi.mocked(aiDataUnderstanding)
const choiceGet = vi.mocked(aiDataChoiceGet)
const choiceSet = vi.mocked(aiDataChoiceSet)
const st = () => useDataFigureStore.getState()
const flush = () => new Promise((r) => setTimeout(r, 0))
const ref = (path: string) => ({ path, sheet: null })
const opt = (id: string) => ({ id, label: id.toUpperCase(), logo: 'openai', vendor: 'x', kind: 'key' as const, model: 'm' })
const choice = (o: Partial<AiDataUnderstandingChoice> = {}): AiDataUnderstandingChoice => ({ state: 'on', mode: 'auto', connection: 'a', options: [opt('a'), opt('b')], ...o })
const ov = (): AiDataOverview => ({
  path: 'a.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 3, columns: 2, layout: null,
  fields: [{ name: 'x', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [{ line: 2, cells: ['1'] }], truncated: false, lead_lines: [], raw_lines: [],
})
const cands = (s: string): AiDataProposeResponse =>
  ({
    kind: 'candidates', session: s, dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
    items: [{ id: `${s}-0`, title_code: 'line_band', why_code: 'line_band', recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: '/t', thumbnail_status: 'pending' }],
    roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x', 'z'], y: ['y', 'z'], group: [] } },
  }) as AiDataProposeResponse
let n = 0

beforeEach(() => {
  resetCommitQueue()
  for (const f of [files, overview, propose, understanding, choiceGet, choiceSet]) f.mockReset()
  n = 0
  overview.mockResolvedValue(ov())
  propose.mockImplementation(async () => cands(`S${++n}`))
  files.mockResolvedValue({ files: [{ path: 'a.csv', size: 1, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 3 } }], truncated: false, understanding: choice() })
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  configureDataFigureHost({ openDrawer: () => {}, openScript: async () => {}, projectWritable: () => true, activeCanvasId: () => null, notify: () => {}, dismissAddedNote: () => {} })
})
afterEach(() => {
  useDataFigureStore.getState().clear()
  vi.useRealTimers()
})
const open = async () => {
  await st().loadFiles()
  st().select(ref('a.csv'))
  await flush()
  await flush()
}

describe('状态', () => {
  it('清单里带的对象整个存下，understandingAvailable = state === on', async () => {
    await st().loadFiles()
    expect(st().understandingChoice).toEqual(choice())
    expect(st().understandingAvailable).toBe(true)
    files.mockResolvedValue({ files: [], truncated: false, understanding: choice({ state: 'off', mode: 'off', connection: null }) })
    await st().loadFiles()
    expect(st().understandingAvailable).toBe(false)
  })

  it('旧后端没有这个对象：choice 为 null，退回老字段', async () => {
    files.mockResolvedValue({ files: [], truncated: false, understanding_available: false })
    await st().loadFiles()
    expect(st().understandingChoice).toBeNull()
    expect(st().understandingAvailable).toBe(false)
  })

  it('轻量 GET 落进同一个对象；请求序号：先发后到的旧响应不盖掉新的', async () => {
    let slow!: (v: { understanding: AiDataUnderstandingChoice }) => void
    choiceGet.mockReturnValueOnce(new Promise((r) => (slow = r))).mockResolvedValueOnce({ understanding: choice({ connection: 'b' }) })
    const first = st().refreshUnderstandingChoice()
    await st().refreshUnderstandingChoice()
    slow({ understanding: choice({ connection: 'old' }) })
    await first
    expect(st().understandingChoice?.connection).toBe('b')
  })

  it('清单在途时用户做了选择：清单里那份旧对象不盖掉', async () => {
    let late!: (v: Awaited<ReturnType<typeof aiDataFilesAll>>) => void
    files.mockReturnValueOnce(new Promise((r) => (late = r)))
    const p = st().loadFiles()
    choiceSet.mockResolvedValue({ understanding: choice({ state: 'off', mode: 'off', connection: null }) })
    await st().setUnderstandingChoice({ mode: 'off' })
    late({ files: [], truncated: false, understanding: choice() })
    await p
    expect(st().understandingChoice?.state).toBe('off')
  })
})

describe('setUnderstandingChoice', () => {
  it('把选择发给后端，成功后存回来的对象', async () => {
    await st().loadFiles()
    choiceSet.mockResolvedValue({ understanding: choice({ mode: 'connection', chosen: 'b', connection: 'b' }) })
    await st().setUnderstandingChoice({ mode: 'connection', connection: 'b' })
    expect(choiceSet.mock.calls[0]![0]).toEqual({ mode: 'connection', connection: 'b' })
    expect(st().understandingChoice).toMatchObject({ chosen: 'b', connection: 'b' })
  })

  it('失败（指定的连接此刻用不了）：抛出，状态不变', async () => {
    await st().loadFiles()
    choiceSet.mockRejectedValue(new ApiError('understanding_connection_unavailable', 400, { code: 'understanding_connection_unavailable' }))
    await expect(st().setUnderstandingChoice({ mode: 'connection', connection: 'zz' })).rejects.toBeInstanceOf(ApiError)
    expect(st().understandingChoice).toEqual(choice())
  })

  it('关了：在途的读懂数据停掉，已填好的列角色与 autoPicks 原样', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await st().loadFiles()
    propose.mockImplementation(async () => ({ ...cands(`S${++n}`), understanding: { id: 'U1', state: 'pending' } }) as AiDataProposeResponse)
    understanding.mockResolvedValue({ id: 'U1', state: 'pending' })
    st().select(ref('a.csv'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)
    expect(st().understanding).toBe(true)
    useDataFigureStore.setState({ answers: { y: 'z' }, autoPicks: { y: 'z' } })
    const roles = st().roles
    choiceSet.mockResolvedValue({ understanding: choice({ state: 'off', mode: 'off', connection: null }) })
    await st().setUnderstandingChoice({ mode: 'off' })
    expect(st().understanding).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)
    expect(understanding).not.toHaveBeenCalled() // 轮询已停
    expect(st()).toMatchObject({ answers: { y: 'z' }, autoPicks: { y: 'z' }, roles })
  })
})

describe('换了在用的连接：对当前这份重跑一次', () => {
  const swap = async (next: Partial<AiDataUnderstandingChoice>) => {
    choiceSet.mockResolvedValue({ understanding: choice({ mode: 'connection', chosen: 'b', connection: 'b', ...next }) })
    await st().setUnderstandingChoice({ mode: 'connection', connection: 'b' })
    await flush()
  }
  it('on → on 且连接变了：重新出候选', async () => {
    await open()
    const before = propose.mock.calls.length
    await swap({})
    expect(propose.mock.calls.length).toBe(before + 1)
  })

  it('连接没变（auto → 指定同一个）：不重跑', async () => {
    await open()
    const before = propose.mock.calls.length
    await swap({ connection: 'a', chosen: 'a' })
    expect(propose.mock.calls.length).toBe(before)
  })

  it('用户动过横轴 / 纵轴 / 分组下拉：不重跑（保护用户的选择）', async () => {
    await open()
    useDataFigureStore.setState({ answers: { y: 'z' } })
    const before = propose.mock.calls.length
    await swap({})
    expect(propose.mock.calls.length).toBe(before)
  })

  it('正在出图：不重跑', async () => {
    await open()
    useDataFigureStore.setState({ drawingId: 'S1-0' })
    const before = propose.mock.calls.length
    await swap({})
    expect(propose.mock.calls.length).toBe(before)
  })

  it('关 → 开 走 noteUnavailable 那条路（宿主汇报「没在用」再「在用」）：重跑一次；动过下拉照样不跑', async () => {
    await open()
    st().noteUnavailable(true) // 关着 / 用不了
    const before = propose.mock.calls.length
    st().noteUnavailable(false)
    await flush()
    expect(propose.mock.calls.length).toBe(before + 1)
    st().noteUnavailable(true)
    useDataFigureStore.setState({ answers: { y: 'z' } })
    st().noteUnavailable(false)
    await flush()
    expect(propose.mock.calls.length).toBe(before + 1)
  })
})

describe('留存的草稿：AI 选择不同不命中', () => {
  it('留着 A（auto），之后选了指定的连接；切回 A 要重新出；选择没变则命中', async () => {
    files.mockResolvedValue({
      files: ['a.csv', 'b.csv'].map((p) => ({ path: p, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows: 3 } })),
      truncated: false,
      understanding: choice(),
    })
    await st().loadFiles()
    const go = async (p: string) => {
      st().select(ref(p))
      await flush()
      await flush()
      st().candidates.forEach((c) => st().thumbSettled(st().session!, c.id, true))
    }
    await go('a.csv')
    await go('b.csv') // 留了 A
    const n0 = propose.mock.calls.length
    st().select(ref('a.csv'))
    expect(propose.mock.calls.length).toBe(n0) // 选择没变：命中
    await go('b.csv') // 又留 A
    choiceSet.mockResolvedValue({ understanding: choice({ mode: 'connection', chosen: 'b', connection: 'b' }) })
    await st().setUnderstandingChoice({ mode: 'connection', connection: 'b' })
    await flush()
    const n1 = propose.mock.calls.length
    st().select(ref('a.csv'))
    await flush()
    await flush()
    expect(propose.mock.calls.length).toBe(n1 + 1)
  })
})
