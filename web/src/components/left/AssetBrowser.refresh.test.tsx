/**
 * 素材面板的「刷新项目」按钮（Prompt 06 §八）。
 *
 * 改造前它只调 `assetStore.load()`——那是"再读一遍已经知道的东西"，磁盘上
 * 新出现的脚本、被改掉的注册表一概看不见。现在它走**统一刷新**，与 SSE
 * 事件同一条路径。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '/p', panels: [] }),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchRegistry: vi.fn().mockResolvedValue({
    source: '', scripts: {}, candidates: [], conflicts: {}, all_scripts: [],
  }),
}))

import { fetchPanels, refreshProject } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

const mockRefresh = vi.mocked(refreshProject)
const mockPanels = vi.mocked(fetchPanels)

let host: HTMLElement
let root: Root

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <AssetBrowser />
      </TooltipProvider>,
    )
  })
}

/** 刷新按钮：按 aria-label 取，顺带证明它有一个 */
const refreshButton = () =>
  host.querySelector<HTMLButtonElement>('button[aria-label="刷新项目"]')

beforeEach(async () => {
  localStorage.clear()
  mockRefresh.mockReset().mockResolvedValue({} as never)
  mockPanels.mockReset().mockResolvedValue({ figures_dir: '/p', panels: [] })
  resetAssetLoadBookkeeping()
  useAssetStore.setState({ panels: [], byId: {}, loaded: true, loading: false, error: null })
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  useUiStore.setState({ status: null, statusTone: 'info' })
  await mount()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('刷新按钮', () => {
  it('有可读的无障碍名，且文案不提 registry / 扫描这类内部词', () => {
    const btn = refreshButton()
    expect(btn).not.toBeNull()
    const label = btn!.getAttribute('aria-label')!
    expect(label).toBe('刷新项目')
    expect(label).not.toMatch(/registry|注册表|stem/i)
  })

  it('调 POST /api/project/refresh，而不是自己再扫一遍', async () => {
    await act(async () => {
      refreshButton()!.click()
    })
    expect(mockRefresh).toHaveBeenCalledWith('manual')
  })

  it('走完之后素材清单也重取了一次（丢事件时的兜底）', async () => {
    await act(async () => {
      refreshButton()!.click()
      await Promise.resolve()
    })
    expect(mockPanels).toHaveBeenCalled()
  })

  it('刷新期间按钮进入忙碌态，走完恢复', async () => {
    let release!: () => void
    mockRefresh.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({} as never)
      }),
    )

    await act(async () => {
      refreshButton()!.click()
    })
    expect(refreshButton()!.disabled).toBe(true)

    await act(async () => {
      release()
      await Promise.resolve()
    })
    expect(refreshButton()!.disabled).toBe(false)
  })

  it('后端刷新失败：显示一条常驻错误，不是静默失败', async () => {
    mockRefresh.mockRejectedValue(new Error('扫描失败: 注册表读不回来'))

    await act(async () => {
      refreshButton()!.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(useUiStore.getState().statusTone).toBe('error')
    expect(useUiStore.getState().status).not.toBeNull()
  })
})
