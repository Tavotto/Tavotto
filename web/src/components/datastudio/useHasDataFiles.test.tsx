/**
 * 工作区这边的使用者（左轨「数据」、空画布的「用数据画一张」）挂上时也刷新一次实验开关：
 * 后端已有打开的项目时直接进工作区、不挂首页，首页那一处刷新走不到，本地缓存为空 / 旧值时入口会整个会话都不出现。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAiCapabilities } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import { useHasDataFiles } from './useHasDataFiles'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAiCapabilities: vi.fn(),
  aiDataFilesAll: vi.fn(async () => ({ files: [], truncated: false })),
}))

function Probe() {
  useHasDataFiles()
  return null
}

describe('useHasDataFiles：不经过首页也刷新开关', () => {
  beforeEach(() => {
    vi.mocked(fetchAiCapabilities).mockReset()
    useAiStore.setState({ caps: null })
    useTafFlagStore.setState({ enabled: false, settled: false })
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('直接以工作区启动（没挂过首页）：缓存是关、后端说开 → 挂上后换成开', async () => {
    vi.mocked(fetchAiCapabilities).mockResolvedValue({ taf_enabled: true } as Awaited<ReturnType<typeof fetchAiCapabilities>>)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(<Probe />))
    await act(async () => {})
    expect(fetchAiCapabilities).toHaveBeenCalledTimes(1)
    expect(useTafFlagStore.getState()).toMatchObject({ enabled: true, settled: true })
    // 再挂一次（别的使用者）不重复问
    await act(async () => root.render(<><Probe /><Probe /></>))
    expect(fetchAiCapabilities).toHaveBeenCalledTimes(1)
    act(() => root.unmount())
  })

  it('读不到当没变：缓存值不动', async () => {
    vi.mocked(fetchAiCapabilities).mockRejectedValue(new Error('x'))
    useTafFlagStore.setState({ enabled: true, settled: false })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(<Probe />))
    await act(async () => {})
    expect(useTafFlagStore.getState().enabled).toBe(true)
    act(() => root.unmount())
  })
})
