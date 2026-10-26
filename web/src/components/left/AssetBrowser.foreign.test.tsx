/**
 * 素材卡的“生成可编辑版本”入口（ADR 0143 修订「项目内入口」）：后端在 readiness 的素材上报 `foreign_source`
 * （同目录同主干名、认得出语言的 R / MATLAB 脚本），卡片就不再说“没有找到生成它的脚本”，改成一句
 * “这张图是 R 画的”+ 一个主按钮；主按钮走主页那张卡的同一个转换入口（`convertStore.start` → `startConvert`）。
 * 前端不自己按后缀配对：没有 `foreign_source` 的图什么都不多。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '/p', panels: [] }),
  refreshProject: vi.fn().mockResolvedValue({}),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
  fetchRegistry: vi.fn().mockResolvedValue({
    source: '', scripts: {}, candidates: [], conflicts: {}, all_scripts: [],
  }),
  startConvert: vi.fn(),
}))

import { startConvert, type PanelCapability, type PanelInfo } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import { useConvertStore } from '@/store/convertStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { resetReadinessBookkeeping, useProjectReadinessStore } from '@/store/projectReadinessStore'
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

const layoutOnly = (over: Partial<PanelCapability> = {}): PanelCapability => ({
  status: 'layout_only',
  reason_code: 'no_source_candidate',
  script: null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  ...over,
})

const panel = (id: string, capability: PanelCapability): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'raster',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  capability,
})

let host: HTMLElement
let root: Root

async function mount(panels: PanelInfo[]) {
  useAssetStore.setState({
    panels,
    byId: Object.fromEntries(panels.map((p) => [p.id, p])),
    loaded: true,
    loading: false,
    error: null,
  })
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

const cardOf = (id: string) => host.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)!
const select = (id: string) => act(async () => cardOf(id).click())
const bar = () => host.querySelector<HTMLElement>('[data-asset-foreign]')
const primary = () => host.querySelector<HTMLButtonElement>('[data-asset-foreign-convert]')

beforeEach(() => {
  localStorage.clear()
  resetAssetLoadBookkeeping()
  resetReadinessBookkeeping()
  useProjectReadinessStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  useUiStore.setState({ registryOpen: false, status: null })
  useConvertStore.setState({ entries: {} })
  useForeignScriptStore.setState({ offer: null })
  vi.mocked(startConvert).mockReset()
  vi.mocked(startConvert).mockReturnValue(new Promise(() => {}))
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('素材卡上的 R / MATLAB 入口', () => {
  it('后端报了同名 R 脚本：一句话“这张图是 R 画的”+ 主按钮；点它发的是 source_language=r 的转换请求', async () => {
    await mount([
      panel('fig3_yield.png', layoutOnly({ foreign_source: { script: 'fig3_yield.R', language: 'r' } })),
    ])
    await select('fig3_yield.png')
    expect(bar()!.textContent).toContain('这张图是 R 画的')
    expect(bar()!.textContent).not.toContain('没有找到生成它的脚本')
    expect(cardOf('fig3_yield.png').getAttribute('aria-label')).not.toContain('没有找到生成它的脚本')
    expect(primary()!.textContent).toBe('生成可编辑版本')

    await act(async () => primary()!.click())
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(vi.mocked(startConvert).mock.calls[0]![0]).toMatchObject({
      script: 'fig3_yield.R',
      source_language: 'r',
    })
    // 与主页同一张卡：提议落在 foreignScriptStore
    expect(useForeignScriptStore.getState().offer).toEqual({ script: 'fig3_yield.R', language: 'r' })
  })

  it('MATLAB：一句话说 MATLAB，请求的 source_language=matlab', async () => {
    await mount([
      panel('b.png', layoutOnly({ foreign_source: { script: 'code/b.m', language: 'matlab' } })),
    ])
    await select('b.png')
    expect(bar()!.textContent).toContain('这张图是 MATLAB 画的')
    await act(async () => primary()!.click())
    expect(vi.mocked(startConvert).mock.calls[0]![0]).toMatchObject({
      script: 'code/b.m',
      source_language: 'matlab',
    })
  })

  it('后端没报 foreign_source 的图：不出这一行，仍说“没有找到生成它的脚本”（前端不自己猜后缀）', async () => {
    await mount([panel('plain.png', layoutOnly())])
    await select('plain.png')
    expect(bar()).toBeNull()
    expect(primary()).toBeNull()
    expect(cardOf('plain.png').getAttribute('aria-label')).toContain('没有找到生成它的脚本')
  })

  it('这个脚本已有进行中的转换：再点只出卡，不重发请求', async () => {
    await mount([
      panel('fig.png', layoutOnly({ foreign_source: { script: 'fig.R', language: 'r' } })),
    ])
    await select('fig.png')
    await act(async () => primary()!.click())
    await act(async () => primary()!.click())
    expect(startConvert).toHaveBeenCalledTimes(1)
  })
})
