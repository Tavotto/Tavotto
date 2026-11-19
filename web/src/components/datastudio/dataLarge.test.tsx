/**
 * 大文件（> 5 MiB）在数据页的两样东西：读的时候一句「正在读一个大文件」+ 行内「取消」（取消之后回到可重读的状态），
 * 以及候选上安静的一小段「N 行里取 K 个点」。小文件的界面一点不变。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { ApiError, aiDataFigureDiscard, aiDataFigurePropose, aiDataFilesAll, aiDataOverview, aiDataProgress, aiDataProgressCancel, type AiDataOverview, type AiDataProgress } from '@/lib/api'
import { LARGE_FILE_BYTES } from '@/lib/dataFigure'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { DataDrawer } from './DataDrawer'
import { DataDraft } from './DataDraft'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataOverview: vi.fn(),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureCommit: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  aiDataProgress: vi.fn(),
  aiDataProgressCancel: vi.fn(),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))

let root: Root
let host: HTMLDivElement
async function mount(ui: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{ui}</TooltipProvider>)
  })
}
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
})
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)]
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => (el as HTMLElement).click())
}
const flush = async () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

const ov = (): AiDataOverview => ({
  path: 'big.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 6_120_000, columns: 3, layout: null,
  fields: [
    { name: 'time', kind: 'numeric', numeric: 6_120_000, text: 0, empty: 0, nan: 0, id_like: false },
    { name: 'signal', kind: 'numeric', numeric: 6_120_000, text: 0, empty: 0, nan: 0, id_like: false },
  ],
  preview_rows: [{ line: 2, cells: ['0', '1.2'] }], truncated: true, lead_lines: [], raw_lines: [],
})

describe('大文件：读取中的一句话与「取消」', () => {
  const files = vi.mocked(aiDataFilesAll)
  const overview = vi.mocked(aiDataOverview)
  const propose = vi.mocked(aiDataFigurePropose)
  const progress = vi.mocked(aiDataProgress)
  const cancelApi = vi.mocked(aiDataProgressCancel)
  const entry = (path: string, size: number) => ({ path, size, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 2, rows: 5 } })
  const item = (id: string, code: string, reduced?: { rows: number; kept: number }) => ({ id, title_code: code, why_code: code, recommended: id === 'i1', unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...(reduced ? { reduced } : {}) })
  const cands = (items: ReturnType<typeof item>[]) => ({ kind: 'candidates' as const, session: 'S1', items, dropped: [], render: 'lazy' as const, header_row: 1, roles: null, defaults: [], mixed_cells: [] })
  const start = async (ui: React.ReactNode = <><DataDrawer /><DataDraft /></>) => {
    await act(async () => useDataFigureStore.getState().loadFiles())
    await mount(ui)
    await flush()
  }
  const open = async (i: number) => {
    await click(qa('[data-studio-file]')[i]!)
    await flush()
  }
  beforeEach(() => {
    for (const f of [files, overview, propose, vi.mocked(aiDataFigureDiscard), progress, cancelApi]) f.mockReset()
    vi.mocked(aiDataFigureDiscard).mockResolvedValue({ ok: true })
    progress.mockResolvedValue({ state: 'none' })
    cancelApi.mockRejectedValue(new Error('offline')) // 默认：取消端点不可用 = 退回「前端不再等」
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
    useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
    files.mockResolvedValue({ files: [entry('big.csv', LARGE_FILE_BYTES + 1), entry('edge.csv', LARGE_FILE_BYTES), entry('small.csv', 1024)], truncated: false })
  })

  it('小文件（含恰好 5 MiB）：读的时候还是原来的「正在读…」，没有取消', async () => {
    overview.mockReturnValue(new Promise(() => {}))
    await start()
    await open(1)
    expect(q('[data-large-read]')).toBeNull()
    expect(document.body.textContent).toContain('正在读…')
    await open(2)
    expect(q('[data-large-read]')).toBeNull()
  })

  it('大文件读概览：一句「正在读一个大文件」+ 行内取消；取消 = 中止请求、回到可重读，晚到的结果不再改界面', async () => {
    let late!: (v: AiDataOverview) => void
    overview.mockReturnValueOnce(new Promise((r) => (late = r)))
    await start()
    await open(0)
    expect(q('[data-large-read]')!.textContent).toContain('正在读一个大文件，要一会儿…')
    expect(q('[data-large-read-cancel]')!.getAttribute('title')).toContain('随时可以重新读')
    const signal = overview.mock.calls[0]![2] as AbortSignal
    expect(signal.aborted).toBe(false)
    await click(q('[data-large-read-cancel]'))
    expect(signal.aborted).toBe(true)
    expect(useDataFigureStore.getState().phase).toBe('cancelled')
    expect(q('[data-studio-cancelled]')!.textContent).toContain('已取消，这个文件没读完。')
    expect(q('[data-data-strip]')).toBeNull() // 没有读到内容：不留永远转着的骨架
    await act(async () => late(ov()))
    await flush()
    expect(useDataFigureStore.getState().phase).toBe('cancelled')
    expect(propose).not.toHaveBeenCalled()
    // 重新读取：回到读取中、再发一次请求，读完出候选
    overview.mockResolvedValueOnce(ov())
    propose.mockResolvedValueOnce(cands([item('i1', 'line_band')]))
    await click(q('[data-studio-retry]'))
    await flush()
    expect(overview).toHaveBeenCalledTimes(2)
    expect(qa('[data-candidate]')).toHaveLength(1)
  })

  it('取消之后在抽屉里再点同一个文件，也会重新读', async () => {
    overview.mockReturnValueOnce(new Promise(() => {}))
    await start()
    await open(0)
    await click(q('[data-large-read-cancel]'))
    overview.mockResolvedValueOnce(ov())
    propose.mockResolvedValueOnce(cands([item('i1', 'line_band')]))
    await open(0)
    await flush()
    expect(overview).toHaveBeenCalledTimes(2)
    expect(qa('[data-candidate]')).toHaveLength(1)
  })

  it('大文件出候选这一步（propose）也一样：上方一句话 + 取消；取消后候选不再上屏', async () => {
    overview.mockResolvedValue(ov())
    let late!: (v: ReturnType<typeof cands>) => void
    propose.mockReturnValueOnce(new Promise((r) => (late = r)))
    await start()
    await open(0)
    expect(q('[data-draft-lead][data-large-read]')!.textContent).toContain('正在读一个大文件')
    expect(qa('[data-candidate-skeleton]')).toHaveLength(4)
    const signal = propose.mock.calls[0]![2] as AbortSignal
    await click(q('[data-large-read-cancel]'))
    expect(signal.aborted).toBe(true)
    expect(q('[data-studio-cancelled]')).not.toBeNull()
    expect(qa('[data-candidate-skeleton]')).toHaveLength(0)
    await act(async () => late(cands([item('i1', 'line_band')])))
    await flush()
    expect(qa('[data-candidate]')).toHaveLength(0)
    expect(useDataFigureStore.getState().phase).toBe('cancelled')
  })

  it('数据条的行数：大表写「612万」，小表（< 1 万）原数字', async () => {
    overview.mockResolvedValue(ov())
    propose.mockResolvedValueOnce(cands([item('i1', 'line_band')]))
    await start()
    await open(0)
    expect(q('[data-data-strip-info]')!.textContent).toBe('前 1 行，共 612万 行 · 3 列')
  })

  const prog = (p: Partial<AiDataProgress>): AiDataProgress => ({ state: 'running', kind: 'overview', fraction: 0.42, indeterminate: false, cancellable: true, elapsed_s: 3, ...p })
  const wait = async (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })

  it('真实进度：请求号随请求发出，行内显示百分比；说不出比例（indeterminate）或 none 就只有那一句话', async () => {
    overview.mockReturnValue(new Promise(() => {}))
    progress.mockResolvedValue(prog({ fraction: 0.428 }))
    await start()
    await open(0)
    const rid = overview.mock.calls[0]![3]
    expect(rid).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    await wait(450)
    expect(progress).toHaveBeenCalledWith(rid, 'pa')
    expect(q('[data-large-read-pct]')!.textContent).toBe('42%')
    progress.mockResolvedValue(prog({ fraction: null, indeterminate: true }))
    await wait(450)
    expect(q('[data-large-read-pct]')).toBeNull()
    expect(q('[data-large-read]')!.textContent).toContain('正在读一个大文件，要一会儿…')
    progress.mockResolvedValue({ state: 'none' })
    await wait(450)
    expect(q('[data-large-read-pct]')).toBeNull()
    expect(q('[data-large-read-cancel]')).not.toBeNull()
  })

  it('后端说不可取消就不给「取消」', async () => {
    overview.mockReturnValue(new Promise(() => {}))
    progress.mockResolvedValue(prog({ cancellable: false }))
    await start()
    await open(0)
    await wait(450)
    expect(q('[data-large-read-pct]')).not.toBeNull()
    expect(q('[data-large-read-cancel]')).toBeNull()
  })

  it('取消成功：调取消端点，原请求回 409 propose_cancelled 后收尾到「已取消」，晚些再点重新读取', async () => {
    let rejectOv!: (e: unknown) => void
    overview.mockReturnValueOnce(new Promise((_r, rej) => (rejectOv = rej)))
    cancelApi.mockResolvedValue(prog({ state: 'cancelled', cancelled: true }))
    await start()
    await open(0)
    const rid = overview.mock.calls[0]![3]
    await click(q('[data-large-read-cancel]'))
    expect(cancelApi).toHaveBeenCalledWith(rid, 'pa')
    expect(useDataFigureStore.getState().phase).toBe('reading') // 以原请求的响应为准
    expect((overview.mock.calls[0]![2] as AbortSignal).aborted).toBe(false) // 不靠前端中止
    await act(async () => rejectOv(new ApiError('已取消', 409, { code: 'propose_cancelled' }, null)))
    await flush()
    expect(useDataFigureStore.getState().phase).toBe('cancelled')
    expect(q('[data-studio-cancelled]')).not.toBeNull()
    expect(q('[data-data-strip]')).toBeNull()
  })

  it('出候选那一步被取消：同样 409 收尾', async () => {
    overview.mockResolvedValue(ov())
    let rejectP!: (e: unknown) => void
    propose.mockReturnValueOnce(new Promise((_r, rej) => (rejectP = rej)))
    cancelApi.mockResolvedValue(prog({ state: 'cancelled', cancelled: true, kind: 'propose' }))
    await start()
    await open(0)
    const body = propose.mock.calls[0]![0] as { client_request_id?: string }
    expect(body.client_request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    await click(q('[data-large-read-cancel]'))
    expect(cancelApi).toHaveBeenCalledWith(body.client_request_id, 'pa')
    await act(async () => rejectP(new ApiError('已取消', 409, { code: 'propose_cancelled' }, null)))
    await flush()
    expect(q('[data-studio-cancelled]')).not.toBeNull()
  })

  it('取消与完成赛跑：取消没生效（cancelled 为假、已完成），以原请求的响应为准，照常出候选', async () => {
    let done!: (v: AiDataOverview) => void
    overview.mockReturnValueOnce(new Promise((r) => (done = r)))
    propose.mockResolvedValueOnce(cands([item('i1', 'line_band')]))
    cancelApi.mockResolvedValue(prog({ state: 'done', cancelled: false }))
    await start()
    await open(0)
    await click(q('[data-large-read-cancel]'))
    expect(useDataFigureStore.getState().phase).toBe('reading')
    await act(async () => done(ov()))
    await flush()
    expect(qa('[data-candidate]')).toHaveLength(1)
    expect(q('[data-studio-cancelled]')).toBeNull()
  })

  it('取消时请求还没登记（state none）：退回前端不再等', async () => {
    overview.mockReturnValueOnce(new Promise(() => {}))
    cancelApi.mockResolvedValue({ state: 'none', cancelled: false })
    await start()
    await open(0)
    await click(q('[data-large-read-cancel]'))
    expect(q('[data-studio-cancelled]')).not.toBeNull()
  })

  it('轮询有界：卸载即停；一次失败就停、不重试', async () => {
    overview.mockReturnValue(new Promise(() => {}))
    progress.mockResolvedValue(prog({}))
    await start()
    await open(0)
    await wait(450)
    const n = progress.mock.calls.length
    expect(n).toBeGreaterThan(0)
    act(() => root.unmount())
    await wait(900)
    expect(progress.mock.calls.length).toBe(n)
  })

  it('轮询失败：只退回一句话，不再重试', async () => {
    overview.mockReturnValue(new Promise(() => {}))
    progress.mockRejectedValue(new Error('x'))
    await start()
    await open(0)
    await wait(1200)
    expect(progress).toHaveBeenCalledTimes(1)
    expect(q('[data-large-read]')).not.toBeNull()
    expect(q('[data-large-read-pct]')).toBeNull()
  })

  it('读过之后的大表标记（不到 5 MiB 但行数多）也算大文件', async () => {
    files.mockResolvedValue({ files: [{ ...entry('rows.csv', 1024), table: { ...entry('rows.csv', 1).table, large: true } }], truncated: false })
    overview.mockReturnValue(new Promise(() => {}))
    await start()
    await open(0)
    expect(q('[data-large-read]')).not.toBeNull()
  })

  it('候选一出来（文字到了）这一句就没了：不再有取消', async () => {
    overview.mockResolvedValue(ov())
    propose.mockResolvedValueOnce(cands([item('i1', 'line_band')]))
    await start()
    await open(0)
    expect(q('[data-large-read]')).toBeNull()
    expect(q('[data-draft-lead]')!.textContent).toBe('正在画…')
  })
})

describe('候选上的「N 行里取 K 个点」', () => {
  const files = vi.mocked(aiDataFilesAll)
  const overview = vi.mocked(aiDataOverview)
  const propose = vi.mocked(aiDataFigurePropose)
  const item = (id: string, code: string, reduced?: { rows: number; kept: number }) => ({ id, title_code: code, why_code: code, recommended: id === 'i1', unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...(reduced ? { reduced } : {}) })
  beforeEach(() => {
    for (const f of [files, overview, propose]) f.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
    useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
    files.mockResolvedValue({ files: [{ path: 'big.csv', size: LARGE_FILE_BYTES * 20, table: { status: 'ok', code: null, delimiter: 'comma', header_row: 1, columns: 2, rows: 6_120_000 } }], truncated: false })
    overview.mockResolvedValue(ov())
  })
  const show = async (items: ReturnType<typeof item>[]) => {
    const { CandidateGrid } = await import('./CandidateGrid')
    await mount(<CandidateGrid items={items.map((i) => ({ ...i, thumb: `/t/${i.id}` }))} thumbs={{}} drawingId={null} onPick={() => {}} onThumb={() => {}} />)
  }

  it('大表的候选：名字那一行右端一小段灰字（数字用「万」），悬停有整句；没有 reduced / 没少画的不显示', async () => {
    await show([
      item('i1', 'line_band', { rows: 6_120_000, kept: 42_113 }),
      item('i2', 'scatter_fit', { rows: 6_120_000, kept: 8_400 }),
      item('i3', 'points_mean'),
      item('i4', 'box_points', { rows: 90_000, kept: 90_000 }),
    ])
    const tiles = qa('[data-candidate]')
    expect(tiles[0]!.querySelector('[data-candidate-reduced]')!.textContent).toBe('612万行里取4.2万个点')
    expect(tiles[0]!.querySelector('[data-candidate-reduced]')!.getAttribute('title')).toBe('整张表612万行，这张图画的是其中有代表性的4.2万个点，最大最小值都留着。')
    expect(tiles[1]!.querySelector('[data-candidate-reduced]')!.textContent).toBe('612万行里取8400个点')
    expect(tiles[2]!.querySelector('[data-candidate-reduced]')).toBeNull()
    expect(tiles[3]!.querySelector('[data-candidate-reduced]')).toBeNull()
    // 名字那一行里（不另起一行）
    expect(tiles[0]!.querySelector('[data-candidate-reduced]')!.parentElement!.className).toContain('flex')
  })
})
