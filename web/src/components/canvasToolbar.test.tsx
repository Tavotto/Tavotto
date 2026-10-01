/**
 * 画布工具搬家（2026-09-30 重设计 A1）：文字 / 标注 / 序号 / 适应从顶栏搬到画布底部的浮动工具条，
 * 缩放菜单搬到画布标签行，写回搬进「⋯」第一项。搬家不许丢入口，也不许换钩子：
 *
 *   * 浮动工具条里 `data-tool="select|text"`、`data-fit-canvas` 都在，点了生效；
 *   * 快速编辑（`mode === 'fast_edit'`）时工具条整个不在（与搬家前顶栏一样）；
 *   * 顶栏里不再有这几颗（一个入口只有一个家）；
 *   * 缩放菜单在标签行里、菜单里仍有「适应画布」；
 *   * 写回是「⋯」菜单第一项，计数 n 在项右侧、没内容时禁用并写出原因。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CanvasTabs } from '@/components/CanvasTabs'
import { CanvasToolbar } from '@/components/CanvasToolbar'
import { TopBar } from '@/components/TopBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let root: Root
let host: HTMLDivElement

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

const q = <T extends HTMLElement = HTMLElement>(sel: string) => host.querySelector<T>(sel)

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_toolbar')
  useUiStore.setState({ tool: 'select' })
  useWorkspaceStore.setState({ mode: 'layout' })
})

afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
  useUiStore.setState({ tool: 'select' })
})

describe('浮动工具条', () => {
  it('带着 data-tool / data-fit-canvas 钩子：选择 / 文字 / 标注 / 序号 / 适应一个不少', async () => {
    await mount(<CanvasToolbar />)
    const bar = q('[data-canvas-toolbar]')
    expect(bar, '浮动工具条没渲染').toBeTruthy()
    expect(bar!.querySelectorAll('[data-tool="select"]')).toHaveLength(1)
    expect(bar!.querySelectorAll('[data-tool="text"]')).toHaveLength(1)
    expect(bar!.querySelectorAll('[data-fit-canvas]')).toHaveLength(1)
    // 选择 / 文字 / 标注 ▾ / 序号 / 适应 = 五颗按钮
    expect(bar!.querySelectorAll('button')).toHaveLength(5)
  })

  it('点「文字」切到文字工具，再点一次回到选择；点「选择」回到选择', async () => {
    await mount(<CanvasToolbar />)
    const text = q('[data-tool="text"]')!
    await act(async () => text.click())
    expect(useUiStore.getState().tool).toBe('text')
    expect(text.getAttribute('data-active')).not.toBeNull()
    await act(async () => text.click())
    expect(useUiStore.getState().tool).toBe('select')
    await act(async () => text.click())
    await act(async () => q('[data-tool="select"]')!.click())
    expect(useUiStore.getState().tool).toBe('select')
  })

  it('点「适应」按页面尺寸取景（与 ⌘1 同一个动作）', async () => {
    await mount(<CanvasToolbar />)
    const calls: number[][] = []
    const real = useViewportStore.getState().fitAnimated
    useViewportStore.setState({ fitAnimated: (w: number, h: number) => void calls.push([w, h]) })
    try {
      await act(async () => q('[data-fit-canvas]')!.click())
    } finally {
      useViewportStore.setState({ fitAnimated: real })
    }
    const page = useDocumentStore.getState().doc.page
    expect(calls).toEqual([[page.w, page.h]])
  })

  it('快速编辑时整条工具条不在（与搬家前顶栏同一条判据）', async () => {
    useWorkspaceStore.setState({ mode: 'fast_edit' })
    await mount(<CanvasToolbar />)
    expect(q('[data-canvas-toolbar]')).toBeNull()
    expect(q('[data-tool]')).toBeNull()
  })
})

describe('顶栏里不再有搬走的入口', () => {
  it('没有文字 / 适应 / 写回按钮，但「⋯」钮在', async () => {
    await mount(<TopBar />)
    expect(q('[data-topbar] [data-tool]')).toBeNull()
    expect(q('[data-topbar] [data-fit-canvas]')).toBeNull()
    expect(q('[data-topbar] [data-zoom-menu]')).toBeNull()
    expect(q('[data-topbar] [data-write-back="open"]')).toBeNull()
    expect(q('[data-topbar] [data-more-menu]')).toBeTruthy()
    // 导出仍是顶栏唯一的锚点
    expect(q('[data-topbar] [data-onboarding-anchor="export"]')).toBeTruthy()
  })
})

describe('缩放菜单在画布标签行', () => {
  it('data-zoom-menu 在标签行里，且只有一个', async () => {
    await mount(<CanvasTabs />)
    expect(host.querySelectorAll('[data-zoom-menu]')).toHaveLength(1)
    expect(q('[data-zoom-readout]')).toBeTruthy()
  })
})

describe('写回在「⋯」菜单第一项', () => {
  const openMenu = async () => {
    const more = q('[data-more-menu]')!
    await act(async () => {
      more.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, ctrlKey: false }))
      more.click()
    })
  }
  const item = () => document.body.querySelector<HTMLElement>('[data-write-back-entry="menu"]')

  it('第一项就是写回；没有可写回内容时禁用', async () => {
    await mount(<TopBar />)
    await openMenu()
    const menu = document.body.querySelector('[role="menu"]')
    expect(menu, '菜单没打开').toBeTruthy()
    // 菜单的第一个子节点（不是「第一个菜单项」：前面垫一条分隔线也算不上第一）
    expect(menu!.firstElementChild).toBe(item())
    expect(item()!.getAttribute('data-disabled')).not.toBeNull()
    expect(item()!.getAttribute('data-write-back')).toBe('open')
  })
})
