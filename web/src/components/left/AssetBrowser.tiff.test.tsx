/**
 * 素材库里的 TIFF（issue #534）：卡片说实话的格式名；范围之外的 TIFF 不给卡片、也不静默消失。
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
}))

import type { PanelInfo, UnsupportedAsset } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetBrowseStore } from '@/store/assetBrowseStore'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'

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

const panel = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'raster',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...over,
})

let host: HTMLElement
let root: Root

async function mount(panels: PanelInfo[], unsupported: UnsupportedAsset[] = []) {
  useAssetStore.setState({
    panels,
    byId: Object.fromEntries(panels.map((p) => [p.id, p])),
    unsupported,
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

const cardOf = (id: string) =>
  host.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)!
const note = () => host.querySelector<HTMLElement>('[data-asset-unsupported]')

beforeEach(() => {
  localStorage.clear()
  resetAssetLoadBookkeeping()
  useAssetBrowseStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('卡片上的格式名', () => {
  it('位图按扩展名说实话：TIFF 是 TIFF，JPEG 是 JPEG，不再一律叫 PNG', async () => {
    await mount([
      panel('a.tif'),
      panel('b.TIFF'),
      panel('c.jpg'),
      panel('d.png'),
      panel('e.pdf', { kind: 'pdf' }),
    ])
    expect(cardOf('a.tif').textContent).toContain('TIFF')
    expect(cardOf('b.TIFF').textContent).toContain('TIFF')
    expect(cardOf('c.jpg').textContent).toContain('JPEG')
    expect(cardOf('c.jpg').textContent).not.toContain('PNG')
    expect(cardOf('d.png').textContent).toContain('PNG')
    expect(cardOf('e.pdf').textContent).toContain('PDF')
  })
})

describe('范围之外的 TIFF', () => {
  const bad: UnsupportedAsset = {
    id: 'raw/scan.tif',
    name: 'scan.tif',
    folder: 'raw',
    code: 'tiff_sample_format',
  }

  it('不给卡片，但逐个说清为什么（文案来自 errors:backend.<code>）', async () => {
    await mount([panel('ok.tif')], [bad])
    expect(host.querySelector(`[data-card="${CSS.escape(bad.id)}"]`)).toBeNull()
    const el = note()
    expect(el).not.toBeNull()
    expect(el!.textContent).toContain('以下文件无法使用')
    expect(el!.textContent).toContain('scan.tif 的像素是浮点数或有符号整数')
    expect(el!.querySelector('li')!.getAttribute('title')).toBe('raw/scan.tif')
  })

  it('整个项目只有一张用不了的 TIFF：说出它，但不说「项目里还没有图」', async () => {
    await mount([], [bad])
    expect(note()!.textContent).toContain('scan.tif')
    expect(host.textContent).not.toContain('项目里还没有图')
  })

  it('搜索只中了用不了的那张：列出它，不说「没有匹配的图」', async () => {
    await mount([panel('ok.tif')], [bad])
    await act(async () => useAssetBrowseStore.getState().setQuery('scan'))
    expect(note()!.textContent).toContain('scan.tif')
    expect(host.textContent).not.toContain('没有匹配的图')
  })

  it('搜索谁都没中：照常说「没有匹配的图」（空态只是让位，没被删掉）', async () => {
    await mount([panel('ok.tif')], [bad])
    await act(async () => useAssetBrowseStore.getState().setQuery('nothing-like-it'))
    expect(note()).toBeNull()
    expect(host.textContent).toContain('没有匹配的图')
  })

  it('跟着同一组筛选走：只看 PDF / 只看已使用 / 搜不到它时不出现', async () => {
    await mount([panel('ok.tif')], [bad])
    for (const filters of [
      { type: 'pdf' as const },
      { usedOnly: true },
      { source: 'other' },
    ]) {
      await act(async () =>
        useAssetBrowseStore.getState().setFilters((f) => ({ ...f, ...filters })),
      )
      expect(note(), JSON.stringify(filters)).toBeNull()
      await act(async () => useAssetBrowseStore.getState().clear())
      expect(note()).not.toBeNull()
    }
    await act(async () => useAssetBrowseStore.getState().setQuery('nothing-like-it'))
    expect(note()).toBeNull()
    await act(async () => useAssetBrowseStore.getState().setQuery('scan'))
    expect(note()).not.toBeNull()
  })

  it('没有代码对应的文案时退回文件名，不显示 i18n 键', async () => {
    await mount([], [{ ...bad, code: 'tiff_from_the_future' }])
    expect(note()!.textContent).toContain('scan.tif')
    expect(note()!.textContent).not.toContain('backend.')
  })
})
