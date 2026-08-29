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

  it('两个出口都在：添加到画布 / 回到画布排版', async () => {
    act(() => openFastEdit('a.pdf'))
    await mount()
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent ?? '')
    expect(labels.some((l) => l.includes('添加到画布'))).toBe(true)
    expect(labels.some((l) => l.includes('画布排版'))).toBe(true)
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
