/**
 * 快速编辑工作区这一屏（Prompt 09）。
 *
 * 判据全部打在**看得见的差别**上，而不是"模式变量是不是那个值"——后者
 * 由 `store/workspace.test.ts` 看护，在这里再断言一遍等于同一条保证有两个
 * 实现，谁坏了都不会红。这里问的是三件事：
 *
 * 1. 这一屏只画那一张图（页面纸、网格、别的对象都让开）；
 * 2. 出口在（添加到画布 / 回画布排版）；
 * 3. **文档一个字节没动**——快速编辑是一种看法，不是一次编辑。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CanvasStage } from './CanvasStage'
import { subscribePruneSelection } from '@/hooks/usePruneSelection'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { openFastEdit, returnToLayout, useWorkspaceStore } from '@/store/workspace'
import { emptyProject } from '@/types/document'
import type { PanelInfo } from '@/lib/api'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

/** jsdom 没有 ResizeObserver；CanvasStage 的视口上报靠它 */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver

const info = (id: string, script?: string): PanelInfo => ({
  id,
  name: id.replace(/\.[^.]+$/, ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...(script ? { script } : {}),
})

let container: HTMLDivElement
let root: Root

const mount = () =>
  act(() => {
    root.render(
      <TooltipProvider>
        <CanvasStage />
      </TooltipProvider>,
    )
  })

beforeEach(async () => {
  localStorage.clear()
  URL.createObjectURL = vi.fn(() => 'blob:mock/1')
  useWorkspaceStore.getState().clear()
  useUiStore.getState().setElementPanel(null)
  const a = info('a.pdf', 'fig.py')
  const b = info('b.pdf', 'fig.py')
  useAssetStore.setState({ panels: [a, b], byId: { 'a.pdf': a, 'b.pdf': b } })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_stage')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const objectIds = () =>
  [...container.querySelectorAll('[data-object-id]')].map((el) =>
    el.getAttribute('data-object-id'),
  )

describe('快速编辑这一屏', () => {
  it('只画当前那一张图，别的对象与页面纸都让开', async () => {
    act(() => {
      openFastEdit('a.pdf')
      openFastEdit('b.pdf')
    })
    await mount()

    const active = useWorkspaceStore.getState().activePanelId
    expect(objectIds()).toEqual([active])
    // 页面纸（PageSheet）是排版的语言：这一屏上不该有它
    expect(container.querySelector('[data-page-sheet]')).toBeNull()
  })

  it('画布排版模式照旧画整张版', async () => {
    act(() => {
      openFastEdit('a.pdf')
      openFastEdit('b.pdf')
      returnToLayout()
    })
    await mount()
    expect(objectIds()).toHaveLength(2)
    expect(container.querySelector('[data-page-sheet]')).not.toBeNull()
  })

  it('两个出口都在：添加到画布 / 返回画布', async () => {
    act(() => openFastEdit('a.pdf'))
    await mount()
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent ?? '')
    expect(labels.some((l) => l.includes('添加到画布'))).toBe(true)
    expect(labels.some((l) => l.includes('返回画布'))).toBe(true)
  })

  it('切进切出不动文档：对象、位置、历史长度全都一样', async () => {
    act(() => openFastEdit('a.pdf'))
    await mount()
    const before = JSON.stringify(useDocumentStore.getState().doc)
    const past = useDocumentStore.getState().past.length

    act(() => returnToLayout())
    act(() => openFastEdit('a.pdf'))
    act(() => returnToLayout())

    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(before)
    expect(useDocumentStore.getState().past.length).toBe(past)
  })

  it('位图没写物理密度时，尺寸旁边说出来它是假定的', async () => {
    const r = {
      ...info('r.png'),
      kind: 'raster' as const,
      original_spec: {
        source_kind: 'raster' as const,
        logical_w_mm: 50.8,
        logical_h_mm: 25.4,
        px_w: 1200,
        px_h: 600,
        dpi: 600,
        dpi_source: 'assumed' as const,
        viewport_pt: null,
        transparent: false,
      },
    }
    useAssetStore.setState({ panels: [r], byId: { 'r.png': r } })
    act(() => openFastEdit('r.png'))
    await mount()
    expect(container.textContent).toContain('假定密度')
  })

  it('源文件不在了：显示的是上一次已知的规格，并且说出来', async () => {
    act(() => openFastEdit('a.pdf'))
    // 素材从清单里消失（文件被删 / 网盘掉线）——文档里那个面板一个字节没动
    useAssetStore.setState({ panels: [], byId: {} })
    await mount()
    expect(container.textContent).toContain('上次已知')
  })

  it('一个来源都没有时不显示一个编出来的尺寸，而是说"尺寸未知"', async () => {
    // 素材清单里有这张图（所以打得开），但它一个尺寸维度都没有
    const blank = { ...info('x.pdf', 'fig.py'), native_w_mm: 0, native_h_mm: 0 }
    useAssetStore.setState({ panels: [blank], byId: { 'x.pdf': blank } })
    act(() => openFastEdit('x.pdf'))
    await mount()
    expect(container.textContent).toContain('尺寸未知')
  })

  it('没有源脚本的图：说清原因并给出下一步，不画成错误', async () => {
    const c = info('c.pdf')
    useAssetStore.setState({ panels: [c], byId: { 'c.pdf': c } })
    act(() => openFastEdit('c.pdf'))
    await mount()
    expect(container.textContent).toContain('连接源脚本')
  })
})

/**
 * UI 审计 T06：「编辑原图」把还不在文档里的图加进来时，浮动条常驻一行说明
 * （撤销即移除）；图本来就在文档里 / 回排版再进来时没有这一行。
 * 用 DOM 断言而不是 store 字段：store 那一维在 workspace.test.ts 里钉。
 */
describe('「刚为编辑加入本文档」的说明', () => {
  const note = () => container.querySelector('[data-fast-edit-added-note]')

  it('加进来的那一次显示；回排版再进同一张（已在文档里）不显示', async () => {
    act(() => openFastEdit('a.pdf'))
    await mount()
    expect(note()).not.toBeNull()
    expect(note()!.textContent).toContain('撤销')

    act(() => returnToLayout())
    act(() => openFastEdit('a.pdf'))
    expect(note()).toBeNull()
  })

  /**
   * 活动区必须**先在、后变**。
   *
   * 读屏播报的是活动区**内容的变化**：把一个已经填好字的 `role="status"` 整个插
   * 进 DOM，各家 AT 行为不一致、很可能一声不吭。所以这条断言钉的不是「有没有这
   * 段文字」（那一维上面两条已经钉了），而是**那块区在没话说的时候也在**——正是
   * 这一维塌了的话，提示会静默失效，而界面看上去完全正常。
   */
  const live = () => container.querySelector('[data-fast-edit-live]')

  it('播报区常驻：没话说时它是空的，但节点在；有话说时同一个节点被填上', async () => {
    // 图本来就在文档里（不是这次加进来的）：没话说
    act(() => openFastEdit('a.pdf'))
    await mount()
    act(() => returnToLayout())
    act(() => openFastEdit('a.pdf'))
    expect(note(), '这一次不该有可见的说明').toBeNull()
    expect(live(), '没话说时播报区也必须在 DOM 里').not.toBeNull()
    expect(live()!.textContent?.trim(), '没话说时它是空的').toBe('')

    // 换一张还不在文档里的：同一个节点被填上，而不是新插一个
    const before = live()
    act(() => returnToLayout())
    act(() => openFastEdit('b.pdf'))
    expect(live()!.textContent, '有话说时播报区被填上').toContain('撤销')
    expect(live(), '必须是同一个节点被填上，不能是新插进来的').toBe(before)
  })

  it('撤销那次加入 → 快速编辑退出，说明跟着消失', async () => {
    // 「对象消失就退出快速编辑」的清扫在 App 层挂（usePruneSelection）：这里手动订阅
    const stopPrune = subscribePruneSelection()
    act(() => openFastEdit('a.pdf'))
    await mount()
    expect(note()).not.toBeNull()
    act(() => useDocumentStore.getState().undo())
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(useWorkspaceStore.getState().addedForEdit).toBeNull()
    expect(note()).toBeNull()
    stopPrune()
  })
})
