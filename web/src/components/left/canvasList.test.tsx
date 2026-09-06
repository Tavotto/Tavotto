/**
 * 画布列表（审计 T05）。验收原话：**三张不同图的画布仅凭缩略图即可区分**。
 *
 * 缩略图此前只画对象包围盒，有内容的画布看上去就是一块灰占位——这里量的是
 * 「它画的是真实内容」：面板挂素材库同一张预览图（按 fileId 各不相同）、
 * 文字画出文字。像素层面「看不看得出区别」jsdom 判不了，判据只能落在
 * **每张画布的缩略图内容互不相同**上。
 *
 * 另外两条：默认命名全产品一个格式（`defaultCanvasName`），列表里能重命名、
 * 复制、上移下移——拖动重排只有鼠标能用，菜单是键盘那条路。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { defaultCanvasName, emptyProject, type CanvasObject } from '@/types/document'
import { CanvasList } from './CanvasList'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const panel = (id: string, fileId: string, x = 0): CanvasObject =>
  ({
    id,
    type: 'panel',
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    overrides: [],
    x,
    y: 0,
    w: 40,
    h: 30,
  }) as CanvasObject

const text = (id: string, s: string): CanvasObject =>
  ({
    id,
    type: 'text',
    text: s,
    x: 5,
    y: 5,
    w: 30,
    h: 6,
    sizePt: 8,
    bold: false,
    color: '#000000',
    align: 'left',
  }) as CanvasObject

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <CanvasList />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

const thumbs = () => [...container.querySelectorAll('[data-canvas-thumb]')] as SVGElement[]
const names = () => useDocumentStore.getState().canvases.map((c) => c.name)

async function seed() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_canvaslist')
  useAssetStore.setState({
    byId: { 'a.pdf': { id: 'a.pdf', mtime: 1 }, 'b.pdf': { id: 'b.pdf', mtime: 2 } },
  } as never)
  const st = useDocumentStore.getState()
  st.commit(literal('准备'), (d) => {
    d.objects = [panel('p1', 'a.pdf'), text('t1', '第一张版上的说明')]
  })
  st.addCanvas()
  useDocumentStore.getState().commit(literal('准备 2'), (d) => {
    d.objects = [panel('p2', 'b.pdf')]
  })
  st.addCanvas()
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useUiStore.setState({ confirm: null })
  await seed()
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

describe('缩略图画的是真实内容', () => {
  it('三张画布的缩略图互不相同', async () => {
    await mount()
    const shapes = thumbs().map((t) => t.innerHTML)
    expect(shapes).toHaveLength(3)
    expect(new Set(shapes).size).toBe(3)
  })

  it('面板挂的是素材库同一条预览图链路，按 fileId 各不相同', async () => {
    await mount()
    const hrefs = [...container.querySelectorAll('[data-thumb-panel]')].map((n) =>
      n.getAttribute('href'),
    )
    expect(hrefs).toHaveLength(2)
    expect(hrefs[0]).toContain('a.pdf')
    expect(hrefs[1]).toContain('b.pdf')
    expect(hrefs[0]).not.toBe(hrefs[1])
  })

  it('文字画出文字本身，不是又一个灰方块', async () => {
    await mount()
    const t = thumbs()[0].querySelector('text')
    expect(t?.textContent).toContain('第一张版上的说明')
  })

  it('空画布的缩略图是空的：不画一个假内容', async () => {
    await mount()
    expect(thumbs()[2].querySelectorAll('*')).toHaveLength(0)
  })
})

describe('默认命名只有一个格式', () => {
  it('第一张与新建的画布同一个生成器', async () => {
    expect(names()).toEqual([defaultCanvasName(1), defaultCanvasName(2), defaultCanvasName(3)])
    // 审计观察到的正是这两种写法混在一行标签里
    expect(names().some((n) => /^Fig \d/.test(n))).toBe(false)
  })

  it('名字被占用时往后找，不撞名', async () => {
    useDocumentStore.getState().renameCanvas(useDocumentStore.getState().canvases[0].id, 'Figure 4')
    useDocumentStore.getState().addCanvas()
    expect(new Set(names()).size).toBe(names().length)
    expect(names()).toContain(defaultCanvasName(5))
  })
})

describe('列表里能管理画布', () => {
  // Radix 的 DropdownMenu 开在 pointerdown 上，jsdom 里 .click() 打不开它
  const menuItems = async (rowIndex: number) => {
    const trigger = [...container.querySelectorAll('button')].filter((b) =>
      (b.getAttribute('aria-label') ?? '').includes('的操作'),
    )[rowIndex] as HTMLButtonElement
    await act(async () => {
      trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      trigger.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0 }))
      trigger.click()
      await new Promise((r) => setTimeout(r, 0))
    })
    return [...document.querySelectorAll('[role=menuitem]')] as HTMLElement[]
  }

  it('上移 / 下移改的是真实顺序', async () => {
    await mount()
    const before = names()
    const items = await menuItems(1)
    const down = items.find((i) => i.textContent?.includes('下移'))!
    await act(async () => down.click())
    const after = useDocumentStore.getState().canvases.map((c) => c.name)
    expect(after).toEqual([before[0], before[2], before[1]])
  })

  it('到头就禁用：第一行不能上移、最后一行不能下移', async () => {
    await mount()
    const first = await menuItems(0)
    expect(first.find((i) => i.textContent?.includes('上移'))?.getAttribute('aria-disabled')).toBe(
      'true',
    )
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    const last = await menuItems(2)
    expect(last.find((i) => i.textContent?.includes('下移'))?.getAttribute('aria-disabled')).toBe(
      'true',
    )
  })

  it('重命名与复制也在同一处菜单里', async () => {
    await mount()
    const items = await menuItems(0)
    const texts = items.map((i) => i.textContent ?? '')
    expect(texts.some((t) => t.includes('重命名'))).toBe(true)
    expect(texts.some((t) => t.includes('复制'))).toBe(true)
  })
})
