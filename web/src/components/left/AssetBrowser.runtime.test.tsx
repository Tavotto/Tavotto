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
  removeRuntimeAsset: vi.fn().mockResolvedValue({ removed: true, id: '' }),
}))
// 落面板的三个动作打桩。**必须回一个真的对象**：快速编辑（Prompt 09）拿它的
// id 当工作区目标，回 undefined 的话崩的是被测代码之外的地方，看起来像产品坏了。
const fakePanel = (id: string) => ({ id: 'obj-1', type: 'panel', fileId: id })
vi.mock('@/store/actions', () => ({
  addPanel: vi.fn((info: { id: string }) => fakePanel(info.id)),
  addRuntimePanel: vi.fn((d: { asset_id: string }) => fakePanel(d.asset_id)),
  enterElementEdit: vi.fn(),
}))

import { probeScript, removeRuntimeAsset, fetchRuntimeAssets, type CapturedFigureDescriptor, type RuntimeAssetInfo } from '@/lib/api'
import { AssetBrowser } from '@/components/left/AssetBrowser'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
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
const mockRemove = vi.mocked(removeRuntimeAsset)
const mockList = vi.mocked(fetchRuntimeAssets)

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
  mockRemove.mockClear()
  useDocumentStore.setState((st) => ({
    doc: { ...st.doc, objects: [] },
    canvases: st.canvases.filter((c) => c.id !== 'cv-other'),
  }))
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('RuntimeAssetCard', () => {
  it('跑过的 runtime 素材：badge + 预览 + 「打开」用描述符（反证 #2）', async () => {
    useRuntimeAssetStore.setState({ assets: [asset()] })
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    expect(card).toBeTruthy()
    expect(card.textContent).toContain('运行时图')
    // 预览来自 runtime preview 端点，不是任何磁盘文件 URL
    const img = card.querySelector('img')!
    expect(img.getAttribute('src')).toContain('/api/runtime/preview')
    // Enter = 打开（快速编辑，Prompt 09）。落面板那一步仍然把**描述符**交给
    // addRuntimePanel——runtime 素材没有 path 字段可用，反证 #2 看护的正是这里
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

  it('stale 素材：卡片里只写状态，「重新运行」在列表外的右键 / ⇧F10 菜单里', async () => {
    useRuntimeAssetStore.setState({ assets: [asset({ status: 'possibly_stale' })] })
    mockProbe.mockImplementation(() => new Promise(() => {}))
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    expect(card.querySelector('[data-runtime-stale]')?.textContent).toContain('可能有变')
    // 颜色只在 14px 图标上：整行文字是 ink-3，不是红字
    const stale = card.querySelector('[data-runtime-stale]')!
    expect(stale.className).toContain('text-ink-3')
    expect(stale.className).not.toMatch(/text-danger/)
    expect(stale.querySelector('svg')!.getAttribute('class')).toContain('text-danger')
    // option 里不嵌可 Tab 控件（axe nested-interactive）
    expect(card.querySelector('button, input, select, textarea, a[href]')).toBeNull()
    expect(document.querySelector('[data-asset-rerun]')).toBeNull()
    const open = () =>
      act(async () => {
        card.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }))
      })
    await open()
    const rerun = document.querySelector<HTMLElement>('[data-asset-rerun]')!
    expect(rerun.closest('[role="option"]')).toBeNull()
    expect(rerun.textContent).toContain('重新运行')
    await act(async () => {
      rerun.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      rerun.click()
    })
    expect(mockProbe).toHaveBeenCalledWith('show.py')
    // 跑着的时候菜单项置灰、文案换成「运行中」，不重复发
    await open()
    const busy = document.querySelector<HTMLElement>('[data-asset-rerun]')!
    expect(busy.hasAttribute('data-disabled')).toBe(true)
    await act(async () => busy.click())
    expect(mockProbe).toHaveBeenCalledTimes(1)
  })

  it('fresh 素材的菜单里没有「重新运行」；也没有选中页脚', async () => {
    useRuntimeAssetStore.setState({ assets: [asset()] })
    await mount()
    const card = host.querySelector<HTMLElement>('[data-card="runtime:show.py#show"]')!
    await act(async () => card.click())
    expect(host.querySelector('[data-asset-footer]')).toBeNull()
    await act(async () => {
      card.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }))
    })
    expect(document.querySelector('[data-asset-card-menu]')).toBeTruthy()
    expect(document.querySelector('[data-asset-rerun]')).toBeNull()
    expect(card.querySelector('[data-runtime-stale]')).toBeNull()
  })
  describe('从素材中移除（脚本已不在磁盘上的运行时图）', () => {
    const ID = 'runtime:show.py#show'
    const openMenu = async () => {
      const card = host.querySelector<HTMLElement>(`[data-card="${ID}"]`)!
      await act(async () => {
        card.dispatchEvent(
          new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }),
        )
      })
    }
    const removeItem = () => document.querySelector<HTMLElement>('[data-asset-remove]')

    it('missing_source 的运行时卡：菜单有这一项，点了以后调用 API（带画布里的 fileId）并刷新清单，卡片消失', async () => {
      useRuntimeAssetStore.setState({ assets: [asset({ status: 'missing_source' })] })
      useDocumentStore.setState((st) => ({
        doc: { ...st.doc, objects: [{ type: 'panel', fileId: 'other.pdf' }] as never },
      }))
      mockList.mockResolvedValue({ assets: [] })
      await mount()
      await openMenu()
      const item = removeItem()!
      expect(item.textContent).toContain('从素材中移除')
      expect(item.hasAttribute('data-disabled')).toBe(false)
      await act(async () => item.click())
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0))
      })
      expect(mockRemove).toHaveBeenCalledWith(ID, ['other.pdf'])
      expect(host.querySelector(`[data-card="${ID}"]`)).toBeNull()
    })

    it('画布在用：置灰，第二行写「画布中正在使用」，点了不调用 API', async () => {
      useRuntimeAssetStore.setState({ assets: [asset({ status: 'missing_source' })] })
      useDocumentStore.setState((st) => ({
        doc: { ...st.doc, objects: [{ type: 'panel', fileId: ID }] as never },
      }))
      await mount()
      await openMenu()
      const item = removeItem()!
      expect(item.hasAttribute('data-disabled')).toBe(true)
      expect(item.textContent).toContain('画布中正在使用')
      await act(async () => item.click())
      expect(mockRemove).not.toHaveBeenCalled()
    })

    it('只在非当前画布上用到：置灰，且请求的 used_ids 含它（未保存的画布也算）', async () => {
      useRuntimeAssetStore.setState({ assets: [asset({ status: 'missing_source' })] })
      useDocumentStore.setState((st) => ({
        doc: { ...st.doc, objects: [] },
        canvases: [
          ...st.canvases,
          {
            id: 'cv-other',
            name: 'B',
            page: st.doc.page,
            objects: [{ type: 'panel', fileId: ID }] as never,
            guides: [],
          },
        ],
      }))
      await mount()
      await openMenu()
      const item = removeItem()!
      expect(item.hasAttribute('data-disabled')).toBe(true)
      await act(async () => item.click())
      expect(mockRemove).not.toHaveBeenCalled()
      // 换成可点的另一张图，验证别的画布上的引用被带进 used_ids
      const OTHER = 'runtime:b.py#b'
      useRuntimeAssetStore.setState({
        assets: [asset({ status: 'missing_source' }), asset({ id: OTHER, script: 'b.py', stem: 'b', status: 'missing_source' })],
      })
      await act(async () => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      })
      const card = host.querySelector<HTMLElement>(`[data-card="${OTHER}"]`)!
      await act(async () => {
        card.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }))
      })
      await act(async () => removeItem()!.click())
      expect(mockRemove).toHaveBeenCalledWith(OTHER, [ID])
    })

    it('脚本还在的运行时卡、以及文件素材：菜单里没有这一项', async () => {
      useRuntimeAssetStore.setState({ assets: [asset({ status: 'fresh' }), asset({ id: 'runtime:b.py#b', script: 'b.py', stem: 'b', status: 'possibly_stale' })] })
      await mount()
      await openMenu()
      expect(document.querySelector('[data-asset-card-menu]')).toBeTruthy()
      expect(removeItem()).toBeNull()
      const stale = host.querySelector<HTMLElement>('[data-card="runtime:b.py#b"]')!
      await act(async () => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      })
      await act(async () => {
        stale.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }))
      })
      expect(removeItem()).toBeNull()
    })
  })
})
