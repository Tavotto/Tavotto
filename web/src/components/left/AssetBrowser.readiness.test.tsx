/**
 * 素材卡的接入状态（Prompt 08 §七 / §八）。
 *
 * 改造前一张图能不能编辑只由 `{}` 那一个图标表达：**有它 = 能编辑，没有它 =
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

import type { PanelCapability, PanelInfo, ReadinessStatus } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import {
  resetReadinessBookkeeping,
  useProjectReadinessStore,
} from '@/store/projectReadinessStore'
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

const cardOf = (id: string) =>
  host.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)!
const notice = () => host.querySelector('[data-capability-notice]')

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

describe('卡片上的状态', () => {
  it('五种不可编辑各有自己的角标文字，不再长得一模一样', async () => {
    const cases: [string, ReadinessStatus, string][] = [
      ['Auto.pdf', 'auto_linkable', '待连接'],
      ['Mystery.pdf', 'needs_probe', '需试运行'],
      ['Dup.pdf', 'conflict', '有冲突'],
      ['Gone.pdf', 'source_missing', '源脚本丢失'],
      ['Photo.pdf', 'layout_only', '仅排版'],
    ]
    await mount(cases.map(([id, st]) => panel(id, { capability: cap(st) })))
    for (const [id, , label] of cases) {
      expect(cardOf(id).textContent, id).toContain(label)
    }
  })

  it('editable 保留 `{}` 紧凑标记，不再加一个写着「可编辑」的角标', async () => {
    await mount([
      panel('Ok.pdf', { script: 'fig.py', capability: cap('editable') }),
    ])
    const card = cardOf('Ok.pdf')
    expect(card.querySelector('svg.lucide-braces')).not.toBeNull()
    expect(card.textContent).not.toContain('可编辑')
  })

  it('状态进 aria-label：读屏器不靠角标的颜色或位置', async () => {
    await mount([panel('Gone.pdf', { capability: cap('source_missing') })])
    expect(cardOf('Gone.pdf').getAttribute('aria-label')).toContain('源脚本丢失')
  })

  it('capability 缺席 = 这一轮还不知道，**不补一个默认状态**', async () => {
    await mount([panel('New.pdf')])
    const card = cardOf('New.pdf')
    expect(card.textContent).not.toContain('仅排版')
    expect(card.getAttribute('aria-label')).not.toContain('仅排版')
  })
})

describe('无障碍：option 里不许再嵌可 Tab 的控件', () => {
  it('每张卡片内部一个 <button> 都没有', async () => {
    await mount([
      panel('Gone.pdf', { capability: cap('source_missing') }),
      panel('Ok.pdf', { script: 'fig.py', capability: cap('editable') }),
    ])
    for (const option of host.querySelectorAll('[role="option"]')) {
      expect(option.querySelector('button'), option.getAttribute('aria-label') ?? '').toBeNull()
      expect(option.querySelector('[tabindex="0"]:not([role="option"])')).toBeNull()
    }
  })

  it('方向键仍然在网格里走（加了角标不该动键盘导航）', async () => {
    await mount([
      panel('A.pdf', { capability: cap('layout_only') }),
      panel('B.pdf', { capability: cap('layout_only') }),
    ])
    await act(async () => {
      cardOf('A.pdf').focus()
      cardOf('A.pdf').dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      )
    })
    expect(document.activeElement).toBe(cardOf('B.pdf'))
  })
})

describe('选中卡片后的说明条', () => {
  it('非可编辑：给出状态、一句原因和一个真按钮', async () => {
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    await act(async () => cardOf('Photo.pdf').click())
    const box = notice()
    expect(box).not.toBeNull()
    expect(box!.textContent).toContain('仅排版')
    expect(box!.textContent).toMatch(/排版|裁剪|导出/)
    expect(box!.querySelector('button')).not.toBeNull()
    // 它在 listbox **外面**
    expect(box!.closest('[role="listbox"]')).toBeNull()
  })

  it('按钮打开接入状态并聚焦到这张图', async () => {
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    await act(async () => cardOf('Photo.pdf').click())
    await act(async () => notice()!.querySelector('button')!.click())
    expect(useUiStore.getState().registryOpen).toBe(true)
    expect(useProjectReadinessStore.getState().focusId).toBe('Photo.pdf')
  })

  it('换一张卡片就跟着换；选中可编辑的那张则整条消失', async () => {
    await mount([
      panel('Gone.pdf', { capability: cap('source_missing') }),
      panel('Ok.pdf', { script: 'fig.py', capability: cap('editable') }),
    ])
    await act(async () => cardOf('Gone.pdf').click())
    expect(notice()!.textContent).toContain('源脚本丢失')
    await act(async () => cardOf('Ok.pdf').click())
    expect(notice()).toBeNull()
  })

  it('capability 还没到：选中了也不说话（"这一轮还不知道"不是一种状态）', async () => {
    await mount([panel('New.pdf')])
    await act(async () => cardOf('New.pdf').click())
    expect(notice()).toBeNull()
  })

  it('没选中任何卡片时不占位', async () => {
    await mount([panel('Photo.pdf', { capability: cap('layout_only') })])
    expect(notice()).toBeNull()
  })
})
