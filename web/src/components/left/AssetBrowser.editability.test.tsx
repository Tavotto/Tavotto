/**
 * 「能不能编辑」胶囊筛选（全部 / 可编辑 / [待连接] / 仅版面）。
 *
 * 归组只翻译后端的 `capability.status`：待连接的那四档不许被并进「仅版面」（卡上写着
 * 「需试运行」，下一步是跑一下），`capability` 缺席时不替后端补 `layout_only`。
 *
 * 下面原样沿用接入状态用例（AssetBrowser.readiness.test.tsx）的挂载与夹具。
 *
 * （原注释）改造前一张图能不能编辑只由 `{}` 那一个图标表达：**有它 = 能编辑，没有它 =
 * 什么都不知道**。五种"不能编辑"在界面上长得一模一样，而它们的下一步完全不同。
 *
 * 这一批同时守住无障碍那条硬约束：卡片是 `role="option"`，**里面不许再有可
 * Tab 的控件**（axe nested-interactive，serious）。所以角标是 `<span>`，
 * 「查看接入状态」那个真按钮住在列表外面的说明条里。
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

import type {
  PanelCapability,
  PanelInfo,
  ReadinessStatus,
  RuntimeAssetInfo,
  UnsupportedAsset,
} from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import {
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from '@/store/projectReadinessStore'
import { useAssetBrowseStore } from '@/store/assetBrowseStore'
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

const cap = (status: ReadinessStatus, over: Partial<PanelCapability> = {}): PanelCapability => ({
  status,
  reason_code:
    status === 'editable'
      ? 'registered_source'
      : status === 'source_missing'
        ? 'registered_script_missing'
        : status === 'conflict'
          ? 'multiple_source_candidates'
          : status === 'needs_probe'
            ? 'runtime_output_unknown'
            : status === 'auto_linkable'
              ? 'static_unique_candidate'
              : 'no_source_candidate',
  script: status === 'editable' || status === 'source_missing' ? 'fig.py' : null,
  candidates: status === 'auto_linkable' ? ['fig.py'] : [],
  can_probe: false,
  can_manual_link: true,
  ...over,
})

const panel = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
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


beforeEach(() => {
  localStorage.clear()
  resetAssetLoadBookkeeping()
  resetReadinessBookkeeping()
  useProjectReadinessStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  useUiStore.setState({ registryOpen: false, status: null })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const group = () => host.querySelector<HTMLElement>('[data-testid="asset-editability"]')
const segment = (value: string) =>
  group()!.querySelector<HTMLButtonElement>(`[role="radio"][data-value="${value}"]`)
const shown = () =>
  [...host.querySelectorAll<HTMLElement>('li[role="option"][data-card]')].map((li) =>
    li.getAttribute('data-card'),
  )
const pick = async (value: string) => {
  await act(async () => segment(value)!.click())
}

beforeEach(() => {
  useAssetBrowseStore.getState().clear()
})

describe('能不能编辑的胶囊筛选', () => {
  const mixed = () => [
    panel('Edit.pdf', { script: 'fig.py', capability: cap('editable') }),
    panel('Photo.pdf', { capability: cap('layout_only') }),
    panel('Scan.png', { kind: 'raster', capability: cap('layout_only') }),
  ]

  it('常驻在搜索框下：全部 / 可编辑 / 仅版面，默认「全部」', async () => {
    await mount(mixed())
    const radios = [...group()!.querySelectorAll('[role="radio"]')]
    expect(radios.map((r) => r.textContent)).toEqual(['全部', '可编辑', '仅版面'])
    expect(segment('all')!.getAttribute('aria-checked')).toBe('true')
    expect(shown()).toHaveLength(3)
  })

  it('「可编辑」只留可编辑的图，「仅版面」只留仅版面的图', async () => {
    await mount(mixed())
    await pick('editable')
    expect(shown()).toEqual(['Edit.pdf'])
    await pick('layout')
    expect(shown().sort()).toEqual(['Photo.pdf', 'Scan.png'])
    await pick('all')
    expect(shown()).toHaveLength(3)
  })

  it('待连接的四档自成一组，不被并进「仅版面」；没有这类图时那一档不出现', async () => {
    await mount([
      ...mixed(),
      panel('Mystery.pdf', { capability: cap('needs_probe') }),
      panel('Gone.pdf', { script: 'gone.py', capability: cap('source_missing') }),
    ])
    expect(segment('pending')).not.toBeNull()
    await pick('layout')
    expect(shown().sort()).toEqual(['Photo.pdf', 'Scan.png'])
    await pick('pending')
    expect(shown().sort()).toEqual(['Gone.pdf', 'Mystery.pdf'])
  })

  it('capability 缺席：有 script 照角标口径算可编辑，没有就不归组（不补 layout_only）', async () => {
    await mount([panel('Known.pdf', { script: 'fig.py' }), panel('New.pdf')])
    await pick('editable')
    expect(shown()).toEqual(['Known.pdf'])
    await pick('layout')
    expect(shown()).toEqual([])
    await pick('all')
    expect(shown().sort()).toEqual(['Known.pdf', 'New.pdf'])
  })

  it('筛到 0 张说「没有匹配」，不说「项目里还没有图」', async () => {
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    await pick('editable')
    expect(host.textContent).toContain('没有匹配的图')
    expect(host.textContent).not.toContain('项目里还没有图')
  })

  it('运行时图：跑过（有描述符）算可编辑，没跑过算待连接', async () => {
    const rt = (stem: string, ran: boolean) =>
      ({
        id: `runtime:${stem}`, script: `${stem}.py`, stem, entry: `${stem}.py`, status: 'fresh',
        cached: ran, size_mm: ran ? [80, 60] : null, capture_source: ran ? 'savefig' : null,
        descriptor: ran ? ({} as never) : null,
      }) as RuntimeAssetInfo
    useRuntimeAssetStore.setState({ assets: [rt('ran', true), rt('fresh', false)] })
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    await pick('editable')
    expect(shown()).toEqual(['runtime:ran'])
    await pick('pending')
    expect(shown()).toEqual(['runtime:fresh'])
  })

  it('项目里一张图都没有时不出这排胶囊', async () => {
    await mount([])
    expect(group()).toBeNull()
  })

  const bad: UnsupportedAsset = {
    id: 'raw/scan.tif',
    name: 'scan.tif',
    folder: 'raw',
    code: 'tiff_sample_format',
  }

  it('选着「仅版面」时最后一张图消失：胶囊仍在，点「全部」能回到用不了的文件（Codex #1027）', async () => {
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    await pick('layout')
    await act(async () => {
      useAssetStore.setState({ panels: [], byId: {}, unsupported: [bad] })
    })
    expect(group()).not.toBeNull()
    expect(segment('layout')!.getAttribute('aria-checked')).toBe('true')
    expect(host.querySelector('[data-asset-unsupported]')).toBeNull()
    await pick('all')
    expect(host.querySelector('[data-asset-unsupported]')).not.toBeNull()
  })

  it('只有用不了的文件、筛选为「全部」时胶囊也在', async () => {
    await mount([], [bad])
    expect(group()).not.toBeNull()
  })

  it('是常驻取值，不再进可移除的筛选标签；旧的「只看可编辑」图标钮已移除', async () => {
    await mount(mixed())
    await pick('editable')
    expect(host.querySelector('[aria-label="已启用筛选"]')).toBeNull()
    expect(document.querySelector('[aria-label="只看可编辑的图"]')).toBeNull()
  })
})
