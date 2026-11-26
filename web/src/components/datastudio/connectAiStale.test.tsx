/**
 * 「连接 AI」胶囊不该卡住：应用刚启动时文件清单比连接状态先回来，后端那一刻只看能力缓存、答「读懂数据不可用」；
 * 随后连接状态到了（有可用的连接，集合此后不再变）——必须重新问一次，胶囊消失。接口全 mock。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataFilesAll, type AiCapabilities } from '@/lib/api'
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
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
  fetchAiCapabilities: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))

let root: Root
let host: HTMLDivElement
const files = vi.mocked(aiDataFilesAll)
const pill = () => document.querySelector('[data-data-connect]')
const capsReady = () => ({ agents: [], vendors: [], connections: [{ id: 'sub:claude', kind: 'subscription', state: 'ready', label: 'Claude', models: [] }] }) as unknown as AiCapabilities
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
/** 后端此刻的回答，用例里改 */
let avail = false

beforeEach(async () => {
  vi.useFakeTimers()
  files.mockReset()
  avail = false
  files.mockImplementation(async () => ({ files: [], truncated: false, understanding_available: avail }))
  setCurrentProjectId('pa')
  useDataFigureStore.getState().clear()
  useAiStore.setState({ caps: null })
  useProjectStore.setState({ project: { open: true, name: 'proj', writable: true } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => { root.render(<TooltipProvider><DataDrawer /><DataDraft /></TooltipProvider>) })
  await settle()
})
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  useDataFigureStore.getState().clear()
  useAiStore.setState({ caps: null })
  vi.useRealTimers()
})

describe('清单先回来、当时后端说不可用', () => {
  it('连接状态随后到了且有可用连接：重取一次清单，后端改口后胶囊消失', async () => {
    expect(files).toHaveBeenCalledTimes(1) // 清单已取过一次，当时回的是不可用
    expect(useDataFigureStore.getState().understandingAvailable).toBe(false)
    avail = true // 能力缓存此刻有了
    await act(async () => useAiStore.setState({ caps: capsReady() }))
    await settle()
    expect(files).toHaveBeenCalledTimes(2)
    expect(useDataFigureStore.getState().understandingAvailable).toBe(true)
    expect(pill()).toBeNull()
  })

  it('后端坚持不可用（只有它用不了的连接）：一份连接状态只再问一次，胶囊照旧在；连接状态刷新了才再问', async () => {
    await act(async () => useAiStore.setState({ caps: capsReady() }))
    await settle()
    await settle()
    expect(files).toHaveBeenCalledTimes(2)
    expect(pill()).not.toBeNull()
    await act(async () => useAiStore.setState({ caps: capsReady() }))
    await settle()
    expect(files).toHaveBeenCalledTimes(3)
    expect(pill()).not.toBeNull()
  })

  it('没有任何可用连接：不重取', async () => {
    await act(async () => useAiStore.setState({ caps: { agents: [], vendors: [], connections: [] } as unknown as AiCapabilities }))
    await settle()
    expect(files).toHaveBeenCalledTimes(1)
    expect(pill()).not.toBeNull()
  })
})
