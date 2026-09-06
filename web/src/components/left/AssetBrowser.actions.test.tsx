/**
 * 素材卡的两个动作（UI 审计 T06）：**编辑原图** 与 **添加到画布** 是两个明确、
 * 各说各的后果的动作，不再由一个叫「打开」的动作暗中承载"加入文档"。
 *
 * 三条到达路径落到同一对 action 上：卡片上的就近入口（鼠标）、Enter /
 * Shift+Enter（键盘）、列表下方的真按钮（读屏）。同源的 runtime 条目与它的
 * 磁盘图相邻并写明关系；搜索词与筛选在组件卸载后还在，换项目才清。
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
// 两个稳定动作打桩：这一份量的是"哪条路调了哪个动作、带的是哪个 id"，
// 动作本身的语义（+1 / 不复制 / 撤销）在 store/workspace.test.ts 里钉
vi.mock('@/store/workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/workspace')>()),
  openFastEdit: vi.fn(() => 'editing'),
  addFigureToLayout: vi.fn(() => 'added'),
}))

import type { CapturedFigureDescriptor, PanelInfo, RuntimeAssetInfo } from '@/lib/api'
import { AssetBrowser, runtimeSiblingOf } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DEFAULT_ASSET_FILTERS, useAssetBrowseStore } from '@/store/assetBrowseStore'
import { resetAssetLoadBookkeeping, useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { addFigureToLayout, openFastEdit } from '@/store/workspace'

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

const mockOpen = vi.mocked(openFastEdit)
const mockAdd = vi.mocked(addFigureToLayout)

const panel = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, '').split('/').pop()!,
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  script: 'fig.py',
  ...over,
})

const desc = (stem: string, script = 'fig.py'): CapturedFigureDescriptor => ({
  asset_id: `runtime:${script}#${stem}`,
  script,
  entry: '__main__',
  stem,
  capture_source: 'savefig',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [120, 90],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

const runtime = (stem: string, over: Partial<RuntimeAssetInfo> = {}): RuntimeAssetInfo => ({
  id: `runtime:fig.py#${stem}`,
  script: 'fig.py',
  stem,
  entry: '__main__',
  status: 'fresh',
  cached: true,
  size_mm: [120, 90],
  capture_source: 'savefig',
  descriptor: desc(stem),
  ...over,
})

let host: HTMLElement
let root: Root | null = null

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(
      <TooltipProvider>
        <AssetBrowser />
      </TooltipProvider>,
    )
  })
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

async function unmount() {
  if (!root) return
  await act(async () => root!.unmount())
  root = null
  host.remove()
}

const seedPanels = (panels: PanelInfo[]) =>
  useAssetStore.setState({
    panels,
    byId: Object.fromEntries(panels.map((p) => [p.id, p])),
    loaded: true,
    loading: false,
    error: null,
  })

const cardOf = (id: string) => host.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)!
const cardIds = () => [...host.querySelectorAll<HTMLElement>('[data-card]')].map((c) => c.dataset.card)
const chips = (card: HTMLElement) =>
  [...card.querySelectorAll<HTMLElement>('span[title]')].filter((s) => s.textContent?.trim())
const actionsBar = () => host.querySelector<HTMLElement>('[data-selected-asset-actions]')
const key = (card: HTMLElement, k: string, shift = false) =>
  act(async () => {
    card.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey: shift, bubbles: true }))
  })

beforeEach(() => {
  localStorage.clear()
  resetAssetLoadBookkeeping()
  seedPanels([])
  useRuntimeAssetStore.getState().clear()
  useScriptRunStore.getState().clear()
  useAssetBrowseStore.getState().clear()
  useUiStore.setState({ status: null })
  mockOpen.mockClear()
  mockAdd.mockClear()
})

afterEach(unmount)

describe('文件卡：两个动作', () => {
  beforeEach(async () => {
    seedPanels([panel('Fig1.pdf')])
    await mount()
  })

  it('就近入口写的是「编辑原图」与「添加到画布」，不再是一个中性的「打开」', () => {
    const labels = chips(cardOf('Fig1.pdf')).map((c) => c.textContent?.trim())
    expect(labels).toContain('编辑原图')
    expect(labels).toContain('添加到画布')
    expect(labels).not.toContain('打开')
  })

  it('点「编辑原图」→ openFastEdit；点「添加到画布」→ addFigureToLayout；各带这张图的 id', async () => {
    const [edit, add] = chips(cardOf('Fig1.pdf'))
    await act(async () => edit.click())
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockAdd).not.toHaveBeenCalled()
    await act(async () => add.click())
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockOpen).toHaveBeenCalledTimes(1)
  })

  it('Enter = 编辑原图，Shift+Enter = 添加到画布；可达名旁声明了这两个键', async () => {
    const card = cardOf('Fig1.pdf')
    expect(card.getAttribute('aria-keyshortcuts')).toContain('Shift+Enter')
    await key(card, 'Enter')
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockAdd).not.toHaveBeenCalled()
    await key(card, 'Enter', true)
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockOpen).toHaveBeenCalledTimes(1)
  })

  it('选中一张卡后列表下方出现两个真按钮（listbox 之外），落到同一对动作上', async () => {
    expect(actionsBar()).toBeNull()
    await act(async () => cardOf('Fig1.pdf').click())
    const bar = actionsBar()!
    expect(bar).toBeTruthy()
    // 真按钮住在 listbox 外面：option 里不许再嵌可 Tab 的控件
    expect(host.querySelector('[role="listbox"]')!.contains(bar)).toBe(false)
    const buttons = [...bar.querySelectorAll('button')]
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(['编辑原图', '添加到画布'])
    await act(async () => buttons[0].click())
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    await act(async () => buttons[1].click())
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
  })
})

describe('运行时图卡', () => {
  it('跑过的：同一对动作（Shift+Enter 添加到画布走 addFigureToLayout，不复制）', async () => {
    useRuntimeAssetStore.setState({ assets: [runtime('show')] })
    await mount()
    const card = cardOf('runtime:fig.py#show')
    expect(chips(card).map((c) => c.textContent?.trim())).toEqual(['编辑原图', '添加到画布'])
    await key(card, 'Enter', true)
    expect(mockAdd).toHaveBeenCalledWith('runtime:fig.py#show')
    expect(mockOpen).not.toHaveBeenCalled()
  })

  it('没跑过的：只有「运行」，Shift+Enter 什么都不做（没有描述符就没有可添加的东西）', async () => {
    useRuntimeAssetStore.setState({
      assets: [runtime('show', { cached: false, descriptor: null, size_mm: null, status: 'needs_rerun' })],
    })
    await mount()
    const card = cardOf('runtime:fig.py#show')
    expect(chips(card).map((c) => c.textContent?.trim())).toEqual(['运行并发现图'])
    await key(card, 'Enter', true)
    expect(mockAdd).not.toHaveBeenCalled()
    await act(async () => card.click())
    expect([...actionsBar()!.querySelectorAll('button')].map((b) => b.textContent?.trim())).toEqual(['运行并发现图'])
  })
})

describe('看大图弹窗的主按钮', () => {
  const dialogAddButton = () =>
    [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(
      (b) => b.textContent?.trim() === '添加到画布',
    )

  it('文件：Space 看大图 → 「添加到画布」走 addFigureToLayout', async () => {
    seedPanels([panel('Fig1.pdf')])
    await mount()
    await key(cardOf('Fig1.pdf'), ' ')
    const btn = dialogAddButton()!
    expect(btn).toBeTruthy()
    await act(async () => btn.click())
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
  })

  it('跑过的运行时图：同一条路（按 id 走 addFigureToLayout，已在文档里就只是聚焦，绝不叠第二份）', async () => {
    useRuntimeAssetStore.setState({ assets: [runtime('show')] })
    await mount()
    await key(cardOf('runtime:fig.py#show'), ' ')
    const btn = dialogAddButton()!
    expect(btn.disabled).toBe(false)
    await act(async () => btn.click())
    expect(mockAdd).toHaveBeenCalledWith('runtime:fig.py#show')
  })

  it('没跑过的运行时图：主按钮禁用（没有描述符就没有可添加的东西）', async () => {
    useRuntimeAssetStore.setState({
      assets: [runtime('show', { cached: false, descriptor: null, size_mm: null, status: 'needs_rerun' })],
    })
    await mount()
    await key(cardOf('runtime:fig.py#show'), ' ')
    expect(dialogAddButton()!.disabled).toBe(true)
  })
})

describe('同源的运行时图与磁盘图', () => {
  it('runtimeSiblingOf：同一脚本 + 同一 stem 才算同源', () => {
    const a = panel('sub/Fig1.pdf', { folder: 'sub' })
    expect(runtimeSiblingOf(runtime('Fig1'), [a])).toBe(a)
    expect(runtimeSiblingOf(runtime('Fig2'), [a])).toBeNull()
    expect(runtimeSiblingOf(runtime('Fig1', { script: 'other.py' }), [a])).toBeNull()
  })

  it('同源的 runtime 条目紧跟在它的磁盘图后面并写明「同源」；无关的仍排在末尾', async () => {
    seedPanels([panel('A.pdf'), panel('sub/Fig1.pdf', { folder: 'sub' })])
    useRuntimeAssetStore.setState({ assets: [runtime('Fig1'), runtime('Zed')] })
    await mount()
    expect(cardIds()).toEqual(['A.pdf', 'sub/Fig1.pdf', 'runtime:fig.py#Fig1', 'runtime:fig.py#Zed'])
    expect(cardOf('runtime:fig.py#Fig1').textContent).toContain('同源：Fig1.pdf')
    expect(cardOf('runtime:fig.py#Zed').textContent).not.toContain('同源')
  })

  it('磁盘图被筛掉时 runtime 条目回到末尾，不指着一张看不见的卡', async () => {
    seedPanels([panel('A.pdf'), panel('sub/Fig1.pdf', { folder: 'sub' })])
    useRuntimeAssetStore.setState({ assets: [runtime('Fig1')] })
    useAssetBrowseStore.getState().setQuery('A.pdf')
    await mount()
    // 搜索词只匹配 A.pdf 与……runtime 的 stem/script 都不含 "A.pdf"，所以 runtime 条目也被筛掉
    expect(cardIds()).toEqual(['A.pdf'])
  })
})

describe('搜索词与筛选活得比组件长', () => {
  it('卸载再挂载（切页签 / 收起抽屉）后输入还在；换项目（clear）才回默认', async () => {
    seedPanels([panel('Fig1.pdf'), panel('Fig2.pdf')])
    await mount()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="搜索面板"]')!
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'Fig2')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(cardIds()).toEqual(['Fig2.pdf'])

    await unmount()
    await mount()
    expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索面板"]')!.value).toBe('Fig2')
    expect(cardIds()).toEqual(['Fig2.pdf'])

    await act(async () => useAssetBrowseStore.getState().clear())
    expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索面板"]')!.value).toBe('')
    expect(cardIds()).toEqual(['Fig1.pdf', 'Fig2.pdf'])
    expect(useAssetBrowseStore.getState().filters).toEqual(DEFAULT_ASSET_FILTERS)
  })
})
