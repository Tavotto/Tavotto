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
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { runtimeSiblingOf } from '@/lib/assetSibling'
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
const cardIds = () => [...host.querySelectorAll<HTMLElement>('li[data-card]')].map((c) => c.dataset.card)
// 就近入口锚在结构（`data-card-add`，2026-10-16 起每张卡只有一个）：文字区的文件名 / 状态也带 title，
// 光按 `span[title]` 找会把它们数进来
const chips = (card: HTMLElement) => [...card.querySelectorAll<HTMLElement>('[data-card-add]')]
/** 右键 / ⇧F10 菜单（挂在 body 的 portal 上，不在 host 里） */
const menuItems = () =>
  [...document.querySelectorAll<HTMLElement>('[data-asset-card-menu] [role="menuitem"]')].map((m) =>
    m.textContent?.trim(),
  )
const rightClick = (card: HTMLElement) =>
  act(async () => {
    card.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 40 }))
  })
const clickMenuItem = (label: string) =>
  act(async () => {
    const item = [...document.querySelectorAll<HTMLElement>('[data-asset-card-menu] [role="menuitem"]')].find((m) =>
      m.textContent?.includes(label),
    )!
    item.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    item.click()
  })
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

  it('卡片上只有一个就近入口「＋添加到画布」，在缩略图右上角；没有「编辑」那一枚', () => {
    const adds = chips(cardOf('Fig1.pdf'))
    expect(adds).toHaveLength(1)
    expect(adds[0].textContent?.trim()).toBe('添加到画布')
    expect(cardOf('Fig1.pdf').querySelector('[data-card-actions]')).toBeNull()
    // 右上角（不压图里的文字）：top / right，不是 bottom
    expect(adds[0].className).toMatch(/\btop-1\.5\b/)
    expect(adds[0].className).toMatch(/\bright-1\.5\b/)
    expect(adds[0].className).not.toMatch(/\bbottom-/)
    // 悬停 / 键盘焦点才出现
    expect(adds[0].className).toContain('opacity-0')
    expect(adds[0].className).toContain('group-hover:opacity-100')
    expect(adds[0].className).toContain('group-focus-visible:opacity-100')
  })

  it('点「＋」→ addFigureToLayout；双击卡片 = 编辑原图（openFastEdit）；各带这张图的 id', async () => {
    await act(async () => chips(cardOf('Fig1.pdf'))[0].click())
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockOpen).not.toHaveBeenCalled()
    await act(async () => {
      cardOf('Fig1.pdf').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    expect(mockAdd).toHaveBeenCalledTimes(1)
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

  it('选中一张卡后下方没有页脚（常驻动作栏已去掉）；option 里也没有可 Tab 的控件', async () => {
    await act(async () => cardOf('Fig1.pdf').click())
    expect(host.querySelector('[data-asset-footer]')).toBeNull()
    expect(host.querySelector('[data-selected-asset-actions]')).toBeNull()
    expect(cardOf('Fig1.pdf').querySelector('button, [tabindex="0"]')).toBeNull()
  })

  it('右键：菜单里有这张卡的全部动作（编辑原图 / 添加到画布），落到同一对 action 上', async () => {
    await rightClick(cardOf('Fig1.pdf'))
    expect(menuItems()).toEqual(['编辑原图', '添加到画布'])
    // 菜单在 listbox 之外
    expect(host.querySelector('[role="listbox"]')!.contains(document.querySelector('[data-asset-card-menu]'))).toBe(false)
    await clickMenuItem('编辑原图')
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    await rightClick(cardOf('Fig1.pdf'))
    await clickMenuItem('添加到画布')
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
  })

  it('键盘：⇧F10 与 ContextMenu 键开同一份菜单（键盘用户到得了每个动作）', async () => {
    await key(cardOf('Fig1.pdf'), 'F10', true)
    expect(menuItems()).toEqual(['编辑原图', '添加到画布'])
    await act(async () => {
      document.querySelector('[data-asset-card-menu]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      )
    })
    await key(cardOf('Fig1.pdf'), 'ContextMenu')
    expect(menuItems()).toEqual(['编辑原图', '添加到画布'])
  })

  it('标题行有两颗真按钮（可聚焦、带标签、listbox 之外）：没选中置灰，选中后落到与快捷键同一对动作', async () => {
    const open = () => host.querySelector<HTMLButtonElement>('[data-asset-open]')!
    const add = () => host.querySelector<HTMLButtonElement>('[data-asset-add]')!
    expect(open().tagName).toBe('BUTTON')
    expect(open().getAttribute('aria-label')).toBe('编辑原图')
    expect(add().getAttribute('aria-label')).toBe('添加到画布')
    expect(host.querySelector('[role="listbox"]')!.contains(open())).toBe(false)
    // 没选中：置灰（不可聚焦，不作用于一个看不见的目标）
    expect(open().disabled).toBe(true)
    expect(add().disabled).toBe(true)
    await act(async () => cardOf('Fig1.pdf').click())
    expect(open().disabled).toBe(false)
    expect(add().disabled).toBe(false)
    open().focus()
    expect(document.activeElement).toBe(open())
    await act(async () => open().click())
    expect(mockOpen).toHaveBeenCalledWith('Fig1.pdf')
    await act(async () => add().click())
    expect(mockAdd).toHaveBeenCalledWith('Fig1.pdf')
  })

  it('没有描述符的运行时图选中后两颗钮仍置灰（没有可编辑 / 可添加的东西）', async () => {
    await act(async () => root?.unmount())
    host.remove()
    seedPanels([])
    useRuntimeAssetStore.setState({
      assets: [runtime('show', { cached: false, descriptor: null, size_mm: null, status: 'needs_rerun' })],
    })
    await mount()
    await act(async () => cardOf('runtime:fig.py#show').click())
    expect(host.querySelector<HTMLButtonElement>('[data-asset-open]')!.disabled).toBe(true)
    expect(host.querySelector<HTMLButtonElement>('[data-asset-add]')!.disabled).toBe(true)
  })

  it('文件名后的「可编辑」小图标有可达名', () => {
    const marker = cardOf('Fig1.pdf').querySelector<HTMLElement>('[role="img"]')
    expect(marker?.getAttribute('aria-label')).toBe('可编辑')
  })
})

describe('运行时图卡', () => {
  it('跑过的：同一对动作（Shift+Enter 添加到画布走 addFigureToLayout，不复制）', async () => {
    useRuntimeAssetStore.setState({ assets: [runtime('show')] })
    await mount()
    const card = cardOf('runtime:fig.py#show')
    expect(chips(card).map((c) => c.textContent?.trim())).toEqual(['添加到画布'])
    await rightClick(card)
    expect(menuItems()).toEqual(['编辑原图', '添加到画布'])
    await act(async () => document.body.click())
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
    await rightClick(card)
    expect(menuItems()).toEqual(['运行并发现图'])
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

/**
 * 区头上的计数是一个 meta 数字，不是一句话。项目里一张图都没有时它不出现——
 * 下面那句空态已经是「项目里还没有图」，一行之隔说两遍（2026-09-15 左栏审计 L04 一族）；
 * 筛到 0 条是另一回事：「0 / 2」告诉你还有 2 张只是没匹配上。
 */
describe('「图」区头的计数', () => {
  const figuresHead = () =>
    [...host.querySelectorAll('h3 button')].find((b) => b.textContent?.startsWith('图'))!

  it('项目里一张图都没有：区头只有「图」，没有 0', async () => {
    seedPanels([])
    await mount()
    expect(figuresHead().textContent).toBe('图')
  })

  it('有图：区头带总数', async () => {
    seedPanels([panel('Fig1.pdf'), panel('Fig2.pdf')])
    await mount()
    expect(figuresHead().textContent).toBe('图2')
  })

  it('筛到 0 条：写成「0 / 2」，不是空白也不是 0', async () => {
    seedPanels([panel('Fig1.pdf'), panel('Fig2.pdf')])
    useAssetBrowseStore.getState().setQuery('zzz')
    await mount()
    expect(cardIds()).toEqual([])
    expect(figuresHead().textContent).toBe('图0 / 2')
  })
})

describe('搜索词与筛选活得比组件长', () => {
  it('卸载再挂载（切页签 / 收起抽屉）后输入还在；换项目（clear）才回默认', async () => {
    // 条目少于 6 个时没有搜索框（2026-10-16），要有 6 张才摆得出来
    seedPanels(['Fig1.pdf', 'Fig2.pdf', 'Fig3.pdf', 'Fig4.pdf', 'Fig5.pdf', 'Fig6.pdf'].map((id) => panel(id)))
    await mount()
    const input = host.querySelector<HTMLInputElement>('input[aria-label="搜索图"]')!
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'Fig2')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(cardIds()).toEqual(['Fig2.pdf'])

    await unmount()
    await mount()
    expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索图"]')!.value).toBe('Fig2')
    expect(cardIds()).toEqual(['Fig2.pdf'])

    await act(async () => useAssetBrowseStore.getState().clear())
    expect(host.querySelector<HTMLInputElement>('input[aria-label="搜索图"]')!.value).toBe('')
    expect(cardIds()).toHaveLength(6)
    expect(useAssetBrowseStore.getState().filters).toEqual(DEFAULT_ASSET_FILTERS)
  })
})
