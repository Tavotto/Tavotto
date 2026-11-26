/**
 * 「连接 AI」胶囊（从数据到图，左栏「数据」抽屉底部居中、悬浮）：没连 AI 时一直在（选没选文件、有没有数据文件都在）；连上后消失，并对当前数据重新出一次候选。
 * 接口全 mock；连接状态来自 `aiStore.caps`（与助手面板空态同一个来源 `hasUsableAi`）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import {
  aiDataFigurePropose,
  aiDataFilesAll,
  aiDataOverview,
  aiDataUnderstanding,
  type AiCapabilities,
  type AiDataOverview,
  type AiDataUnderstanding,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useConnectAiStore } from '@/store/connectAiStore'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { DataDraft } from './DataDraft'
import { DataDrawer } from './DataDrawer'
import { DataPanel } from './DataPanel'

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
  aiDataUnderstanding: vi.fn(),
  fetchAiCapabilities: vi.fn(() => new Promise(() => {})),
  aiDataProgressCancel: vi.fn(async () => ({ cancelled: false, state: 'none' })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))

let root: Root
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const files = vi.mocked(aiDataFilesAll)
const overview = vi.mocked(aiDataOverview)
const propose = vi.mocked(aiDataFigurePropose)
const poll = vi.mocked(aiDataUnderstanding)

const ov = (): AiDataOverview => ({
  path: 'growth.csv', sheet: null, status: 'ok', code: null, delimiter: 'comma', header_row: 1, rows: 144, columns: 4, layout: null,
  fields: [{ name: 'day', kind: 'numeric', numeric: 3, text: 0, empty: 0, nan: 0, id_like: false }],
  preview_rows: [], truncated: false, lead_lines: [], raw_lines: [],
})
const tbl = (path: string) => ({ path, size: 1, table: { status: 'ok' as const, code: null, delimiter: 'comma' as const, header_row: 1, columns: 3, rows: 144 } })
const roles = { x: 'day', y: ['tumor_volume'], group: 'group', facet: null, unit: null, options: { x: ['day', 'week'], y: ['tumor_volume', 'day'], group: ['group', 'arm'] } }
const cands = (extra: Record<string, unknown> = {}) => ({
  kind: 'candidates' as const, session: 'S1', dropped: [], render: 'lazy' as const, header_row: 1, roles, defaults: [], mixed_cells: [],
  items: [{ id: 'i1', title_code: 'line_band', why_code: 'line_band', recommended: true, unplotted: { count: 0, code: null }, thumbnail_url: '/t/1', thumbnail_status: 'pending' as const }],
  ...extra,
})
const pending: AiDataUnderstanding = { id: 'U1', state: 'pending' }
const ready = (answers: Record<string, unknown>): AiDataUnderstanding => ({ id: 'U1', state: 'ready', answers, header_rows: {}, roles: [], dropped: 0 })

/** 没有任何连接 / 有一个可用的连接（自带 Key、账号登录、订阅登录同一个判据：连接 state = ready） */
const capsNone = { agents: [], vendors: [], connections: [] } as unknown as AiCapabilities
const capsWith = (kind: string) => ({ agents: [], vendors: [], connections: [{ id: 'c1', kind, state: 'ready', label: 'x', models: [] }] }) as unknown as AiCapabilities
/** 后端的 `understanding_available`：undefined = 响应里没有这个字段（旧后端）；`setCaps` 默认让它跟「有没有连接」一致 */
let backendAvail: boolean | undefined
const setCaps = (caps: AiCapabilities | null, avail: boolean | undefined = caps === capsNone ? false : caps === null ? undefined : true) => {
  backendAvail = avail
  return act(async () => useAiStore.setState({ caps }))
}
const ui = <TooltipProvider><DataDrawer /><DataDraft /><DataPanel /></TooltipProvider>
const mountAll = () => act(async () => { root.render(ui) })

const store = () => useDataFigureStore.getState()
const tick = async (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const pick = async (name: string) => { await act(async () => store().select({ path: name, sheet: null })); await settle(); await settle() }
const pills = () => document.querySelectorAll('[data-data-panel] header p button')

beforeEach(async () => {
  vi.useFakeTimers()
  for (const f of [files, overview, propose, poll]) f.mockReset()
  setCurrentProjectId('pa')
  store().clear()
  useAiStore.setState({ caps: null })
  useConnectAiStore.setState({ open: false })
  useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
  backendAvail = undefined
  files.mockImplementation(async () => ({ files: [tbl('growth.csv'), tbl('a.csv'), tbl('b.csv')], truncated: false, ...(backendAvail === undefined ? {} : { understanding_available: backendAvail }) }))
  overview.mockResolvedValue(ov())
  propose.mockResolvedValue(cands())
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await mountAll()
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  store().clear()
  useAiStore.setState({ caps: null })
  useConnectAiStore.setState({ open: false })
  vi.useRealTimers()
})

describe('胶囊出现与否', () => {
  it('没连 AI、抽屉开着、还没选文件：出现，文字复用「连接 AI」，悬停一句话；点击打开现有的连接弹窗；不在右栏标题行', async () => {
    await setCaps(capsNone)
    await settle()
    const pill = q('[data-data-connect]')!
    expect(pill).not.toBeNull()
    expect(store().selected).toBeNull()
    expect(pill.textContent).toBe('连接 AI 以供智能分析')
    expect(pill.getAttribute('title')).toBe('连上后会帮你看懂每一列，选对横轴、纵轴和分组')
    expect(pill.closest('[data-data-panel]')).toBeNull()
    expect(useConnectAiStore.getState().open).toBe(false)
    await act(async () => pill.click())
    expect(useConnectAiStore.getState().open).toBe(true)
  })

  it('选了文件后照样在抽屉里（不在右栏），且只有一枚', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    expect(document.querySelectorAll('[data-data-connect]').length).toBe(1)
    expect(q('[data-data-panel] [data-data-connect]')).toBeNull()
  })

  it('悬浮、不占布局：绝对定位，不拦截条外的点击；清单底部留出内边距，滚到底最后一行露在它上面；连上后内边距收回', async () => {
    await setCaps(capsNone)
    await settle()
    const wrap = q('[data-data-connect]')!.parentElement!
    expect(wrap.className).toContain('absolute')
    expect(wrap.className).toContain('pointer-events-none')
    expect(q('[data-data-connect]')!.className).toContain('pointer-events-auto')
    const list = q('[data-studio-data] ul[role=tree]')!
    expect(list.className).toContain('pb-16') // 4rem ≥ 胶囊 h-9 (2.25rem) + 离底边 bottom-4 (1rem)
    await setCaps(capsWith('key'))
    expect(q('[data-studio-data] ul[role=tree]')!.className).not.toContain('pb-16')
  })

  it('抽屉里没有数据文件（空态）也显示', async () => {
    files.mockResolvedValue({ files: [], truncated: false })
    await act(async () => store().loadFiles())
    await setCaps(capsNone)
    await settle()
    await settle()
    expect(q('[data-studio-no-files]')).not.toBeNull()
    expect(q('[data-data-connect]')).not.toBeNull()
  })

  it('一直显示：不自动消失、没有关闭按钮（过了很久、候选出完还在）', async () => {
    await setCaps(capsNone)
    await settle()
    await tick(60_000)
    expect(q('[data-data-connect]')).not.toBeNull()
    expect(document.querySelectorAll('[data-data-connect] button').length).toBe(0)
    expect(q('[data-data-connect]')!.textContent).toBe('连接 AI 以供智能分析')
  })

  it('连接状态未知（还没探测完）：不出现；探测完且没连才出现', async () => {
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
    await setCaps(capsNone)
    expect(q('[data-data-connect]')).not.toBeNull()
  })

  it.each(['key', 'account', 'subscription'])('已连接（%s 类连接）：不出现', async (kind) => {
    await setCaps(capsWith(kind))
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('等待灰字、「试试建议」出现时右栏标题行里没有「连接 AI」，标题行最多一枚胶囊', async () => {
    await setCaps(capsNone)
    propose.mockResolvedValueOnce(cands({ understanding: pending }))
    poll.mockResolvedValue(ready({ x: 'week' }))
    await pick('growth.csv')
    expect(q('[data-data-reading]')).not.toBeNull()
    expect(q('[data-data-panel] [data-data-connect]')).toBeNull()
    expect(pills().length).toBeLessThanOrEqual(1)
    // 用户动过下拉后建议到达 → 「试试建议」胶囊，没有「连接 AI」
    propose.mockResolvedValue(cands())
    await act(async () => store().setRole('group', 'arm'))
    await tick()
    await tick()
    expect(q('[data-data-suggest]')).not.toBeNull()
    expect(q('[data-data-panel] [data-data-connect]')).toBeNull()
    expect(pills().length).toBe(1)
  })

  it('合并作图（多份文件）时同样出现', async () => {
    await setCaps(capsNone)
    await act(async () => store().selectGroup([{ path: 'a.csv', sheet: null }, { path: 'b.csv', sheet: null }]))
    await settle()
    await settle()
    expect(q('[data-data-connect]')).not.toBeNull()
    expect(pills().length).toBe(0)
  })
})

describe('连上之后', () => {
  it('没动过下拉：胶囊消失，对当前数据重新出一次候选，且带上读懂数据', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    expect(propose).toHaveBeenCalledTimes(1)
    propose.mockResolvedValueOnce(cands({ understanding: pending }))
    await setCaps(capsWith('subscription'))
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
    expect(propose).toHaveBeenCalledTimes(2)
    expect(q('[data-data-reading]')).not.toBeNull()
    // 再刷新 caps（仍是已连接）不会再重跑
    await setCaps(capsWith('subscription'))
    await settle()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('动过横轴 / 纵轴 / 分组下拉：只让胶囊消失，不重跑', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    await act(async () => store().setRole('group', 'arm'))
    await settle()
    expect(propose).toHaveBeenCalledTimes(2)
    await setCaps(capsWith('key'))
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('正在出图：不重跑', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    await act(async () => useDataFigureStore.setState({ drawingId: 'i1' }))
    await setCaps(capsWith('key'))
    await settle()
    expect(propose).toHaveBeenCalledTimes(1)
  })

  it('一开始就已连接（第一次探测完）：不算「刚连上」，不重跑', async () => {
    await pick('growth.csv')
    await setCaps(capsWith('key'))
    await settle()
    expect(propose).toHaveBeenCalledTimes(1)
  })
})

describe('后端说「读懂数据」可不可用（订阅登录等 hasUsableAi 为真但后端还不支持）', () => {
  it('后端说不可用而 hasUsableAi 为真（只连了订阅）：胶囊仍在，也不重跑', async () => {
    backendAvail = false
    await act(async () => store().loadFiles()) // 清单响应带着 `understanding_available: false`
    await setCaps(capsNone)
    await pick('growth.csv')
    expect(q('[data-data-connect]')).not.toBeNull()
    expect(propose).toHaveBeenCalledTimes(1)
    await setCaps(capsWith('subscription'), false)
    await settle()
    await settle()
    expect(q('[data-data-connect]')).not.toBeNull()
    expect(propose).toHaveBeenCalledTimes(1)
  })

  it('后端说可用：胶囊不在', async () => {
    backendAvail = true
    await act(async () => store().loadFiles())
    await setCaps(capsNone, true)
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('字段缺失（旧后端 / 取失败）：退回 hasUsableAi', async () => {
    await setCaps(capsNone, undefined)
    await settle()
    expect(store().understandingAvailable).toBeNull()
    expect(q('[data-data-connect]')).not.toBeNull()
    await setCaps(capsWith('key'), undefined)
    await settle()
    expect(q('[data-data-connect]')).toBeNull()
  })

  it('连接集合变了（caps 刷新）→ 重取清单，拿到新的「可不可用」；集合没变的刷新不重取', async () => {
    await setCaps(capsNone)
    await settle()
    const n = files.mock.calls.length
    await setCaps(capsNone)
    await settle()
    expect(files.mock.calls.length).toBe(n)
    await setCaps(capsWith('key'))
    await settle()
    await settle()
    expect(files.mock.calls.length).toBe(n + 1)
    expect(store().understandingAvailable).toBe(true)
  })
})

describe('观察不依赖右栏 / 抽屉是否挂着', () => {
  it('右栏（DataPanel）没挂：连上后仍然重新出一次候选', async () => {
    await act(async () => root.render(<TooltipProvider><DataDrawer /><DataDraft /></TooltipProvider>))
    await setCaps(capsNone)
    await pick('growth.csv')
    expect(q('[data-data-panel]')).toBeNull()
    expect(propose).toHaveBeenCalledTimes(1)
    await setCaps(capsWith('key'))
    await settle()
    await settle()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('变化发生时整个数据页都没挂：之后再挂上，补跑一次且只一次', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    expect(store().sawUnavailable).toBe(true)
    await act(async () => root.render(<div />)) // 抽屉、草稿、右栏全卸载
    await setCaps(capsWith('key'))
    await act(async () => store().loadFiles()) // 变化发生期间别处刷新了清单
    await settle()
    expect(propose).toHaveBeenCalledTimes(1)
    await mountAll()
    await settle()
    await settle()
    expect(propose).toHaveBeenCalledTimes(2)
    await setCaps(capsWith('key'))
    await settle()
    expect(propose).toHaveBeenCalledTimes(2)
  })

  it('补跑也守条件：动过下拉 / 出图中不跑', async () => {
    await setCaps(capsNone)
    await pick('growth.csv')
    await act(async () => store().setRole('x', 'week'))
    await settle()
    const n = propose.mock.calls.length
    await act(async () => root.render(<div />))
    await setCaps(capsWith('key'))
    await act(async () => store().loadFiles())
    await mountAll()
    await settle()
    expect(propose.mock.calls.length).toBe(n)
    expect(store().sawUnavailable).toBe(false)
  })
})
