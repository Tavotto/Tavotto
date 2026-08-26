/**
 * 素材库「图」区的 RuntimeFigureAsset 卡片（Session 5）：
 * badge、来源、添加到画布走**描述符**（负向反证 #2：要求磁盘路径这里红）、
 * 未运行卡片的主动作是「运行并发现图」、键盘可用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '/p', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchRegistry: vi.fn().mockResolvedValue({
    source: '',
    scripts: {},
    candidates: [],
    conflicts: {},
    all_scripts: [],
  }),
  probeScript: vi.fn(),
}))
vi.mock('@/store/actions', () => ({
  addPanel: vi.fn(),
  addRuntimePanel: vi.fn(),
}))

import { probeScript, type CapturedFigureDescriptor, type RuntimeAssetInfo } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// jsdom 没有 ResizeObserver；素材网格用它量列宽，这里只需要不炸
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

const mockAdd = vi.mocked(addRuntimePanel)
const mockProbe = vi.mocked(probeScript)

const desc: CapturedFigureDescriptor = {
  asset_id: 'runtime:show.py#show',
  script: 'show.py',
  entry: '__main__',
  stem: 'show',
  capture_source: 'pyplot',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [120, 90],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
}

const asset = (over: Partial<RuntimeAssetInfo> = {}): RuntimeAssetInfo => ({
  id: 'runtime:show.py#show',
  script: 'show.py',
  stem: 'show',
  entry: '__main__',
  status: 'fresh',
  cached: true,
  size_mm: [120, 90],
  capture_source: 'pyplot',
  descriptor: desc,
  ...over,
})

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
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

beforeEach(() => {
  localStorage.clear()
  useAssetStore.setState({ panels: [], byId: {}, loaded: true, loading: false, error: null })
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  mockAdd.mockClear()
  mockProbe.mockReset()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('RuntimeAssetCard', () => {
  it('跑过的 runtime 素材：badge + 预览 + 「加入画布」用描述符（反证 #2）', async () => {
    useRuntimeAssetStore.setState({ assets: [asset()] })
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    expect(card).toBeTruthy()
    expect(card.textContent).toContain('运行时图')
    // 预览来自 runtime preview 端点，不是任何磁盘文件 URL
    const img = card.querySelector('img')!
    expect(img.getAttribute('src')).toContain('/api/runtime/preview')
    // Enter = 加入画布：把**描述符**交给 addRuntimePanel（没有 path 字段可用）
    await act(async () => {
      card.focus()
      card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(mockAdd).toHaveBeenCalledWith(desc)
  })

  it('还没跑过的 runtime 素材：占位 + 主动作是运行，不给假尺寸假路径', async () => {
    useRuntimeAssetStore.setState({
      assets: [asset({ cached: false, descriptor: null, size_mm: null, status: 'needs_rerun' })],
    })
    mockProbe.mockImplementation(() => new Promise(() => {}))
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    expect(card.textContent).toContain('尚未运行')
    expect(card.querySelector('img')).toBeNull()
    await act(async () => {
      card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(mockAdd).not.toHaveBeenCalled()
    expect(mockProbe).toHaveBeenCalledWith('show.py')
  })

  it('stale 素材显示状态与「重新运行」', async () => {
    useRuntimeAssetStore.setState({ assets: [asset({ status: 'possibly_stale' })] })
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    expect(card.textContent).toContain('可能已变化')
    expect(card.textContent).toContain('重新运行')
  })
})
