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

/**
 * 工具与动作分两种外观（2026-10-07 设计审计 §10.1）：工具 = 32 圆形图标钮、激活 = 墨色实底 + aria-pressed；
 * 动作 = 28 带字 ghost 钮；中间一道竖线。条里 ←/→ 挪焦点（ARIA toolbar），鼠标点工具不拿焦点。
 * 主语：认 `data-tool` / `data-toolbar-item` / `data-fit-canvas`，判的是 aria-pressed 与按钮的 `h-8 / h-7` 档。
 */
describe('浮动工具条：工具 vs 动作', () => {
  it('工具 32 + aria-pressed，激活是墨色实底；动作 28', async () => {
    await mount(<CanvasToolbar />)
    const select = q('[data-tool="select"]')!
    const text = q('[data-tool="text"]')!
    expect(select.className).toContain('h-8')
    expect(select.getAttribute('aria-pressed')).toBe('true')
    expect(select.className).toContain('bg-ink')
    expect(text.getAttribute('aria-pressed')).toBe('false')
    expect(text.className).not.toContain('bg-ink')
    expect(q('[data-fit-canvas]')!.className).toContain('h-7')
    await act(async () => text.click())
    expect(q('[data-tool="text"]')!.getAttribute('aria-pressed')).toBe('true')
    expect(q('[data-tool="select"]')!.getAttribute('aria-pressed')).toBe('false')
  })

  it('条里 → / End 挪焦点，事件被认领（不推画布上的选中对象）', async () => {
    await mount(<CanvasToolbar />)
    const items = [...host.querySelectorAll<HTMLElement>('[data-toolbar-item]')]
    expect(items.length).toBe(5)
    items[0].focus()
    const ev = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })
    act(() => {
      items[0].dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(items[1])
    act(() => {
      items[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }))
    })
    expect(document.activeElement).toBe(items[4])
  })

  // Codex #833：鼠标打开标注菜单、点「插入形状」——Radix 关菜单时默认把焦点还给触发器，下一个 ← / → 就被
  // 工具条吃掉（换焦点），推不动刚插入的形状。指针打开的回到打开前的焦点；键盘打开的照旧回到触发器
  describe('标注菜单关掉后的焦点', () => {
    const trigger = () => q('[data-tool-menu="annotate"]')!
    const firstShape = () =>
      [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((el) =>
        el.textContent?.includes('三角'),
      )!
    const shapes = () => useDocumentStore.getState().doc.objects.length

    it.each([
      ['焦点原在别处', true],
      ['焦点原在 body', false],
    ])('鼠标打开、插入形状（%s）：焦点不落回触发器', async (_n, elsewhere) => {
      await mount(<CanvasToolbar />)
      const other = document.createElement('button')
      document.body.appendChild(other)
      if (elsewhere) other.focus()
      await act(async () => {
        trigger().dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, ctrlKey: false }))
      })
      expect(document.body.querySelector('[role="menu"]'), '菜单没打开').toBeTruthy()
      await act(async () => firstShape().click())
      expect(document.body.querySelector('[role="menu"]')).toBeNull()
      expect(shapes()).toBe(1)
      expect(document.activeElement).not.toBe(trigger())
      if (elsewhere) expect(document.activeElement).toBe(other)
    })

    it('键盘打开、插入形状：焦点回到触发器', async () => {
      await mount(<CanvasToolbar />)
      trigger().focus()
      await act(async () => {
        trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      })
      expect(document.body.querySelector('[role="menu"]'), '菜单没打开').toBeTruthy()
      await act(async () => firstShape().click())
      expect(document.body.querySelector('[role="menu"]')).toBeNull()
      expect(shapes()).toBe(1)
      expect(document.activeElement).toBe(trigger())
    })
  })

  it('鼠标按下工具不拿焦点（点完「选择」接着按方向键是在微调对象）', async () => {
    await mount(<CanvasToolbar />)
    const ev = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
    act(() => {
      q('[data-tool="select"]')!.dispatchEvent(ev)
    })
    expect(ev.defaultPrevented).toBe(true)
  })
})
