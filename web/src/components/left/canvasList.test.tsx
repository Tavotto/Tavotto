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

  it('空画布的缩略图是空的：只有那张纸，不画一个假内容', async () => {
    await mount()
    const g = thumbs()[2].querySelector('g[clip-path]')!
    expect(g.children).toHaveLength(0)
    expect(thumbs()[2].querySelector('[data-thumb-page]')).toBeTruthy()
  })

  it('画的是页面矩形（方向看得出）：横版与竖版的纸不一样，盒子本身透明', async () => {
    // 默认页面是横的（150×100）：把激活画布改成竖版 A4
    await act(async () => {
      useDocumentStore.getState().commit(literal('竖版'), (d) => {
        d.page = { w: 210, h: 297 }
      })
    })
    await mount()
    const ratio = (t: SVGElement) => {
      const r = t.querySelector('[data-thumb-page]')!
      return Number(r.getAttribute('width')) / Number(r.getAttribute('height'))
    }
    const landscape = thumbs().find((t) => ratio(t) > 1)
    const portrait = thumbs().find((t) => ratio(t) < 1)
    expect(portrait, '激活画布改成了竖版').toBeTruthy()
    expect(landscape, '其余画布仍是横版').toBeTruthy()
    for (const t of thumbs()) {
      // 白底与边线画在页面矩形上，不画在盒子上（此前横竖两种页面的缩略图是同一个白框）
      expect(t.getAttribute('class')).not.toMatch(/\bbg-|\bborder\b/)
      expect(Number(t.querySelector('[data-thumb-page]')!.getAttribute('rx'))).toBeGreaterThan(0)
    }
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

describe('行内改名', () => {
  const openBtn = (i: number) =>
    container.querySelectorAll<HTMLButtonElement>('[data-canvas-open]')[i]
  const renameBox = () => container.querySelector<HTMLInputElement>('[data-canvas-rename]')
  const key = async (el: Element, k: string) => {
    await act(async () => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    })
  }
  const type = async (el: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(el, value)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  it('F2 开始改名；改名框不嵌在按钮里（兄弟，不是父子）', async () => {
    await mount()
    await key(openBtn(0), 'F2')
    const box = renameBox()!
    expect(box).not.toBeNull()
    expect(box.closest('button')).toBeNull()
    expect(box.closest('[data-canvas-row]')).toBe(
      container.querySelectorAll('[data-canvas-row]')[0],
    )
    // 改名期间这一行的「打开」按钮让位
    expect(container.querySelectorAll('[data-canvas-open]')).toHaveLength(2)
  })

  it('Enter 提交新名字，焦点回到这一行的按钮', async () => {
    await mount()
    await key(openBtn(0), 'F2')
    await type(renameBox()!, '新名字')
    await key(renameBox()!, 'Enter')
    expect(renameBox()).toBeNull()
    expect(names()[0]).toBe('新名字')
    expect(document.activeElement).toBe(openBtn(0))
  })

  it('Esc 放弃后再改名，草稿从当前名字重新起步', async () => {
    await mount()
    const before = names()[1]
    await key(openBtn(1), 'F2')
    await type(renameBox()!, '没提交的草稿')
    await key(renameBox()!, 'Escape')
    expect(renameBox()).toBeNull()
    expect(names()[1]).toBe(before)
    await key(openBtn(1), 'F2')
    expect(renameBox()!.value).toBe(before)
  })
})

describe('拖动重排', () => {
  const rows = () => [...container.querySelectorAll<HTMLElement>('[data-canvas-row]')]
  const fire = async (el: Element, type: string) => {
    const e = new Event(type, { bubbles: true, cancelable: true })
    await act(async () => {
      el.dispatchEvent(e)
    })
    return e
  }

  it('内部拖动：把第 1 张落到第 3 张上，顺序真的变了', async () => {
    await mount()
    const before = names()
    const [a, , c] = rows()
    await fire(a, 'dragstart')
    expect((await fire(c, 'dragover')).defaultPrevented).toBe(true)
    await fire(c, 'drop')
    expect(names()).toEqual([before[1], before[2], before[0]])
  })

  it('拖动取消后，外部拖进来的东西不接、顺序不动（起点不会一直挂着）', async () => {
    await mount()
    const before = names()
    const [a, b] = rows()
    await fire(a, 'dragstart')
    await fire(a, 'dragend') // Esc / 松在列表外：没有 drop
    expect((await fire(b, 'dragover')).defaultPrevented).toBe(false)
    await fire(b, 'drop')
    expect(names()).toEqual(before)
  })
})

describe('行与键位契约（2026-10-07 设计审计 §10.3）', () => {
  const rowsEl = () => [...container.querySelectorAll<HTMLElement>('[data-canvas-row]')]
  const key = (el: Element, k: string, init: KeyboardEventInit = {}) =>
    act(() => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init }))
    })

  it('带缩略图的行是 lg 52；「+」在（没有外壳时就地画出的）动作槽里，搜索行只有搜索', async () => {
    await mount()
    expect(rowsEl()[0].className).toContain('min-h-13')
    expect(container.querySelector('[data-canvas-new]')).toBeTruthy()
  })

  it('⌥↓ 把这一张往下挪一格（与菜单的「下移」同一个动作）', async () => {
    await mount()
    const before = names()
    await act(async () => {
      rowsEl()[0].querySelector<HTMLElement>('[data-canvas-open]')!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowDown', altKey: true, bubbles: true, cancelable: true }),
      )
    })
    expect(names()).toEqual([before[1], before[0], before[2]])
  })

  it('一列一个 Tab 停靠点，↑↓ 在行间走；⇧F10 开出行菜单', async () => {
    await mount()
    const opens = () => [...container.querySelectorAll<HTMLButtonElement>('[data-canvas-open]')]
    expect(opens().filter((b) => b.tabIndex === 0)).toHaveLength(1)
    act(() => opens()[0].focus())
    key(opens()[0], 'ArrowDown')
    expect(document.activeElement).toBe(opens()[1])
    key(opens()[1], 'F10', { shiftKey: true })
    expect(document.querySelector('[role="menu"]')).not.toBeNull()
  })

  it('行菜单开着时 ↑↓ / Home / End 是菜单的：焦点在菜单项之间走，列表不碰下面的行、Tab 停靠点不挪', async () => {
    await mount()
    const opens = () => [...container.querySelectorAll<HTMLButtonElement>('[data-canvas-open]')]
    // 在中间那一行上开菜单：列表若把方向键当成自己的，会把焦点（与 Tab 停靠点）挪到别的行
    act(() => opens()[1].focus())
    key(opens()[1], 'F10', { shiftKey: true })
    const menu = () => document.querySelector<HTMLElement>('[role="menu"]')
    const items = () => [...menu()!.querySelectorAll<HTMLElement>('[role="menuitem"]:not([data-disabled])')]
    expect(menu(), '⇧F10 没开出菜单').not.toBeNull()
    expect(items().length).toBeGreaterThan(2)
    // 菜单在 portal 里（DOM 上不在列表里），但 React 事件沿组件树冒泡到列表的 onKeyDown
    expect(container.contains(menu())).toBe(false)
    act(() => items()[0].focus())
    // 焦点被抢到行上那一下，Radix 的焦点陷阱会立刻拉回菜单——所以得在行上记下来
    const stolen: string[] = []
    for (const b of opens()) b.addEventListener('focus', () => stolen.push(b.textContent ?? ''))
    const walk = async (k: string) => {
      await act(async () => {
        document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
        await new Promise((r) => setTimeout(r, 0))
      })
      expect(menu(), `${k} 把菜单关掉了`).not.toBeNull()
      expect(stolen, `${k} 被列表当成了「走行」，焦点被挪到了下面的行`).toEqual([])
    }
    await walk('ArrowDown')
    expect(document.activeElement).toBe(items()[1])
    await walk('End')
    expect(document.activeElement).toBe(items().at(-1))
    await walk('Home')
    expect(document.activeElement).toBe(items()[0])
    await walk('ArrowUp')
    expect(menu()!.contains(document.activeElement)).toBe(true)
    expect(opens().map((b) => b.tabIndex), 'Tab 停靠点仍是开菜单的那一行').toEqual([-1, 0, -1])
  })

  it('拖到另一张上画落点线，松手按真实顺序挪', async () => {
    await mount()
    const [a, , c] = rowsEl()
    act(() => {
      a.dispatchEvent(new Event('dragstart', { bubbles: true }))
      c.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }))
    })
    expect(c.querySelector<HTMLElement>(':scope > span[aria-hidden]')!.className).toContain('bg-accent')
  })
})
