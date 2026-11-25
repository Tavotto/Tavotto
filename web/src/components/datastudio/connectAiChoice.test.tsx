/**
 * 连接状态变化时的重新询问（`useConnectAiWatch`），有「用哪个 AI」对象时：`none` / `unavailable` 且前端看到有可用的连接才重取，
 * 用轻量的 GET（不是整份清单）；`off` 绝不重取；旧后端（没有对象）仍走重取清单。接口全 mock。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataChoiceGet, aiDataFilesAll, type AiCapabilities, type AiDataUnderstandingChoice } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { DataDraft } from './DataDraft'
import { DataDrawer } from './DataDrawer'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFilesAll: vi.fn(),
  aiDataChoiceGet: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  fetchAiCapabilities: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
const files = vi.mocked(aiDataFilesAll)
const get = vi.mocked(aiDataChoiceGet)
let root: Root
let host: HTMLDivElement
const capsOf = (ids: string[]) => ({ agents: [], vendors: [], connections: ids.map((id) => ({ id, kind: 'key', state: 'ready', label: id, models: [] })) }) as unknown as AiCapabilities
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const choice = (o: Partial<AiDataUnderstandingChoice>): AiDataUnderstandingChoice => ({ state: 'none', mode: 'auto', connection: null, options: [], ...o })
const pill = () => document.querySelector('[data-data-connect]')
const icon = () => document.querySelector('[data-data-ai-choice]')
/** 清单里带的是什么 */
let first: AiDataUnderstandingChoice

const start = async () => {
  files.mockImplementation(async () => ({ files: [], truncated: false, understanding: first }))
  await act(async () => { root.render(<TooltipProvider><DataDrawer /><DataDraft /></TooltipProvider>) })
  await settle()
}
beforeEach(() => {
  vi.useFakeTimers()
  files.mockReset()
  get.mockReset()
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useAiStore.setState({ caps: null })
  useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  useDataFigureStore.getState().clear()
  useAiStore.setState({ caps: null })
  vi.useRealTimers()
})

describe('有「用哪个 AI」对象', () => {
  it('none + 前端看到有可用连接：用轻量 GET 重取一次（不重取清单），后端改口后胶囊换成图标', async () => {
    first = choice({ state: 'none' })
    await start()
    expect(files).toHaveBeenCalledTimes(1)
    get.mockResolvedValue({ understanding: choice({ state: 'on', connection: 'a', options: [{ id: 'a', label: 'A', logo: 'openai', vendor: 'v', kind: 'key', model: 'm' }] }) })
    await act(async () => useAiStore.setState({ caps: capsOf(['a']) }))
    await settle()
    expect(get).toHaveBeenCalledTimes(1)
    expect(files).toHaveBeenCalledTimes(1)
    expect(pill()).toBeNull()
    expect(icon()).not.toBeNull()
  })

  it('unavailable + 有可用连接：同样重取（指定的那个也许刚连上）', async () => {
    first = choice({ state: 'unavailable', mode: 'connection', chosen: 'a' })
    await start()
    get.mockResolvedValue({ understanding: first })
    await act(async () => useAiStore.setState({ caps: capsOf(['b']) }))
    await settle()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('off：连接状态怎么变都不为这个重取；图标在、没有胶囊', async () => {
    first = choice({ state: 'off', mode: 'off' })
    await start()
    get.mockResolvedValue({ understanding: first })
    await act(async () => useAiStore.setState({ caps: capsOf(['a']) }))
    await settle()
    await act(async () => useAiStore.setState({ caps: capsOf(['a']) })) // 同一集合、新的一份状态
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(files).toHaveBeenCalledTimes(1)
    expect(pill()).toBeNull()
    expect(icon()!.getAttribute('data-data-ai-choice')).toBe('off')
  })

  it('on：同样不为这个重取', async () => {
    first = choice({ state: 'on', connection: 'a' })
    await start()
    await act(async () => useAiStore.setState({ caps: capsOf(['a']) }))
    await settle()
    expect(get).not.toHaveBeenCalled()
  })

  it('连接集合真的变了（新连上一个）：刷新列表用的对象（轻量 GET），off 也一样便宜地更新选项', async () => {
    first = choice({ state: 'off', mode: 'off' })
    await start()
    await act(async () => useAiStore.setState({ caps: capsOf(['a']) }))
    await settle()
    get.mockResolvedValue({ understanding: first })
    await act(async () => useAiStore.setState({ caps: capsOf(['a', 'b']) }))
    await settle()
    expect(get).toHaveBeenCalledTimes(1)
    expect(files).toHaveBeenCalledTimes(1)
  })
})
