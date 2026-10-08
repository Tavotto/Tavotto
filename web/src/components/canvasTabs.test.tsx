/**
 * 画布标签条的形（2026-09-15 打磨 B1 / B2 / T8）。
 *
 * 此前它只借了 `TAB_UNDERLINE` 那条线：选中态是 400 + ink，而右栏页签是 600 + ink
 * ——同一屏两种「选中」。下划线又画在整个 tab 上（含 × 那 20px），线比字宽。
 *
 * 三件事逐条钉：
 *   1. 选中态走 `tabClass`（600），与右栏页签同一副语法；
 *   2. 下划线挂在**文字盒**上，不是整个 tab；
 *   3. 加粗宽度先量后锁——切页签时邻居不挪（`ui/Tabs.tsx` 的 `Tab` 用同一手法）。
 *
 * jsdom 没有布局引擎，`getBoundingClientRect` 恒为 0，量不出真实宽度。所以第 3 条
 * 钉的是**那段逻辑跑过**（量完把 minWidth 写回内联样式），像素归真浏览器。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CanvasTabs } from '@/components/CanvasTabs'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { tabClass } from '@/components/ui/tabClass'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

let root: Root
let host: HTMLDivElement

const mount = () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root.render(
      <TooltipProvider>
        <CanvasTabs />
      </TooltipProvider>,
    )
  })
}

const tabs = () => [...host.querySelectorAll('[role=tab]')] as HTMLElement[]
/** 一个 tab 里承载文字与下划线的那层 */
const nameBox = (t: HTMLElement) => t.firstElementChild as HTMLElement

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_tabs')
  const canvas = (id: string, name: string) => ({
    id,
    name,
    page: { w: 150, h: 100 },
    objects: [],
    guides: [],
  })
  useDocumentStore.setState({
    canvases: [canvas('c1', 'Figure 1'), canvas('c2', 'Figure 2')],
    openTabs: ['c1', 'c2'],
    activeCanvasId: 'c1',
    doc: { ...useDocumentStore.getState().doc, name: 'Figure 1' },
  })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('画布标签条', () => {
  it('选中态与右栏页签同一副语法：`tabClass` 的 600 + ink', () => {
    mount()
    const [first, second] = tabs()
    expect(first.getAttribute('aria-selected')).toBe('true')
    // 钉的是**选中 / 未选中这两套词**取自 `tabClass`，不是把它整串抄一遍：
    // 共同的那几类会被 tab 自己的类合并掉，抄整串等于钉一件不成立的事
    const only = (a: string, b: string) => a.split(' ').filter((c) => !b.split(' ').includes(c))
    for (const cls of only(tabClass(true), tabClass(false))) {
      expect(first.className, '选中态的词来自 tabClass').toContain(cls)
    }
    for (const cls of only(tabClass(false), tabClass(true))) {
      expect(second.className, '未选中态的词来自 tabClass').toContain(cls)
    }
    expect(first.className, '选中 = 600').toContain('font-semibold')
    expect(second.className, '未选中不加粗').not.toContain('font-semibold')
  })

  it('条高 44：与右栏页签条同高（2026-09-30 重设计，两条底边 hairline 连成一条）；页签填满条的内容盒，不另写高度', () => {
    mount()
    const strip = host.querySelector('[data-canvas-tabs]') as HTMLElement
    expect(strip.parentElement!.className, '条本身 44').toContain('h-11')
    // 条带 border-b，内容盒只剩 35：页签再写 h-9 就纵向多出 1px，横滚条的 overflow-y 被算成
    // auto，WebKit 画出一根竖滚动条。像素由 e2e/canvas-tabs-scroll.spec.ts 在真浏览器里量
    for (const t of tabs()) {
      expect(t.className).toContain('h-full')
      expect(t.className).not.toContain('h-9')
    }
  })

  it('下划线挂在文字盒上，不是整个 tab——可关闭的那个不会把线延到 × 底下', () => {
    mount()
    const [first, second] = tabs()
    // 线在里层那个 span 上，且铺满它（inset-x-0 = 文字宽）
    expect(nameBox(first).className).toContain('after:')
    expect(nameBox(first).className).toContain('after:inset-x-0')
    expect(first.className, 'tab 自己不再画线').not.toContain('after:inset-x-1.5')
    // 未选中的那个一条线都没有：tablist 上只有一条
    expect(nameBox(second).className).not.toContain('after:bg-ink')
  })

  it('左缘与品牌标同一条竖线：tab 自己没有左内边距（条的 px-3 就是那 12）', () => {
    mount()
    const [first] = tabs()
    expect(first.className).not.toContain('px-2.5')
    expect(first.className).not.toContain('px-6')
  })

  it('加粗宽度先量后锁：量宽那段真的跑过，minWidth 写回了内联样式', () => {
    // jsdom 的 getBoundingClientRect 恒为 0，effect 里 `w > 0` 不成立、不会写回——
    // 桩一个非零宽度，才量得到「那段逻辑有没有执行」
    const proto = Element.prototype
    const real = proto.getBoundingClientRect
    proto.getBoundingClientRect = function () {
      return { ...real.call(this), width: 48, height: 36 } as DOMRect
    }
    try {
      mount()
      for (const t of tabs()) {
        expect(nameBox(t).style.minWidth, '每个页签都锁了自己的加粗宽度').toBe('48px')
      }
    } finally {
      proto.getBoundingClientRect = real
    }
  })

  it('页签上没有「未保存」记号：文档级 dirty 来回翻，每个页签的 DOM 一字不变（2026-10-07 审计 P0）', () => {
    // 保存状态是整份文档的事，只在顶栏文档名旁说。此前当前页签拿文档级 dirty 画一个点：
    // 哪页激活哪页「未保存」，自动保存每轮还让它闪一秒
    act(() => useDocumentStore.setState({ dirty: false }))
    mount()
    const snapshot = () =>
      [...host.querySelectorAll<HTMLElement>('[data-canvas-tab]')].map((t) => t.outerHTML)
    const clean = snapshot()
    expect(clean).toHaveLength(2)
    act(() => useDocumentStore.setState({ dirty: true }))
    expect(snapshot(), 'dirty 置位后页签不变').toEqual(clean)
  })

  it('当前页签认稳定的 data 钩子：每个页签带自己的 id，只有当前那一个带 data-active', () => {
    mount()
    const hooked = () =>
      tabs().map((t) => [t.getAttribute('data-canvas-tab'), t.hasAttribute('data-active')])
    expect(hooked()).toEqual([
      ['c1', true],
      ['c2', false],
    ])
    act(() => useDocumentStore.setState({ activeCanvasId: 'c2' }))
    expect(hooked()).toEqual([
      ['c1', false],
      ['c2', true],
    ])
  })

  it('当前页签的位置或宽度、条宽变了就再滚进视野；无关的重渲染不把用户横滑走的条拽回来', () => {
    // jsdom 没有布局：按 data-canvas-tab 桩出页签的 offsetLeft / offsetWidth，条宽 120，
    // 条的 scrollLeft 用一个普通字段顶上（jsdom 的 setter 什么都不做）。挂载前就桩好：
    // 首次渲染量到的就是这套几何
    const geo: Record<string, [number, number]> = { c1: [0, 80], c2: [100, 80] }
    let scrollLeft = 0
    let stripWidth = 120
    const isStrip = (el: Element) => el.hasAttribute('data-canvas-tabs')
    const of = (el: Element, i: 0 | 1) => geo[el.getAttribute('data-canvas-tab') ?? '']?.[i] ?? 0
    const stubs: [object, string, PropertyDescriptor][] = [
      [HTMLElement.prototype, 'offsetLeft', { get(this: Element) { return of(this, 0) } }],
      [HTMLElement.prototype, 'offsetWidth', { get(this: Element) { return of(this, 1) } }],
      [Element.prototype, 'clientWidth', { get(this: Element) { return isStrip(this) ? stripWidth : 0 } }],
      [
        Element.prototype,
        'scrollLeft',
        {
          get(this: Element) { return isStrip(this) ? scrollLeft : 0 },
          set(this: Element, v: number) { if (isStrip(this)) scrollLeft = v },
        },
      ],
    ]
    const real = stubs.map(([o, k]) => Object.getOwnPropertyDescriptor(o, k)!)
    for (const [o, k, d] of stubs) Object.defineProperty(o, k, { configurable: true, ...d })
    // 窗口 / 抽屉改宽度不经过 React：只有 ResizeObserver 知道。jsdom 没有它，桩一个手动触发的
    const resized: (() => void)[] = []
    const realRO = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
      constructor(cb: () => void) {
        resized.push(cb)
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    try {
      mount()
      expect(scrollLeft).toBe(0)

      // 用户横滑到第二个页签那里；随后一次与页签几何无关的重渲染（画布表换了引用、内容没变）
      scrollLeft = 100
      act(() => useDocumentStore.setState({ canvases: [...useDocumentStore.getState().canvases] }))
      expect(scrollLeft, '当前页签没动：不许把条拽回来').toBe(100)

      // 当前页签改了名、变宽了（activeId 与 openTabs 都没变）：它在条外，要滚回来
      geo.c1 = [0, 96]
      act(() =>
        useDocumentStore.setState({ doc: { ...useDocumentStore.getState().doc, name: '更长的名字' } }),
      )
      expect(scrollLeft, '当前页签变了几何：滚进视野').toBe(0)

      // 条变窄（没有重渲染）：当前页签右半截出界，ResizeObserver 里要滚到它的右缘
      stripWidth = 60
      act(() => resized.forEach((cb) => cb()))
      expect(scrollLeft, '条变窄：滚到当前页签右缘').toBe(96 - 60)
    } finally {
      stubs.forEach(([o, k], i) => Object.defineProperty(o, k, real[i]))
      globalThis.ResizeObserver = realRO
    }
  })
})

/**
 * 键盘（2026-10-07 设计审计 §10.1，ARIA tabs 模式）：整条一个 Tab 停靠点（roving tabindex）、←/→ 挪焦点、
 * F2 改名、Delete / ⌘W 关、⌥← / ⌥→ 重排；× 不进 Tab 顺序。主语：页签认 `data-canvas-tab`，× 认
 * `data-canvas-tab-close`，改名框认 `data-canvas-tab-rename`。
 */
describe('画布标签条的键盘', () => {
  const tab = (id: string) => host.querySelector<HTMLElement>(`[data-canvas-tab="${id}"]`)!
  const key = (el: HTMLElement, init: KeyboardEventInit) => {
    const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
    act(() => {
      el.dispatchEvent(ev)
    })
    return ev
  }
  const flush = () => act(async () => new Promise<void>((r) => requestAnimationFrame(() => r())))

  it('只有当前画布那个页签在 Tab 顺序里；× 永远不在', () => {
    mount()
    expect(tab('c1').tabIndex).toBe(0)
    expect(tab('c2').tabIndex).toBe(-1)
    for (const x of host.querySelectorAll<HTMLElement>('[data-canvas-tab-close]')) expect(x.tabIndex).toBe(-1)
  })

  it('→ 把焦点挪到下一个页签（不切画布），Enter 才切', async () => {
    mount()
    tab('c1').focus()
    key(tab('c1'), { key: 'ArrowRight' })
    await flush()
    expect(document.activeElement).toBe(tab('c2'))
    expect(tab('c2').tabIndex).toBe(0)
    expect(useDocumentStore.getState().activeCanvasId, '方向键不切画布').toBe('c1')
  })

  it('F2 进入改名；Delete 关掉这个页签，而且不冒到全局（不删画布上的选中对象）', () => {
    mount()
    key(tab('c2'), { key: 'F2' })
    expect(host.querySelector('[data-canvas-tab-rename]')).not.toBeNull()
    act(() => {
      ;(host.querySelector('[data-canvas-tab-rename]') as HTMLElement).blur()
    })
    const seen: string[] = []
    const spy = (e: Event) => seen.push((e as KeyboardEvent).key)
    window.addEventListener('keydown', spy)
    const ev = key(tab('c2'), { key: 'Delete' })
    window.removeEventListener('keydown', spy)
    expect(ev.defaultPrevented).toBe(true)
    expect(seen, 'Delete 没冒到 window 上的全局快捷键').toEqual([])
    expect(useDocumentStore.getState().openTabs).toEqual(['c1'])
  })

  it('⌥→ 把页签往右挪一格（与拖动同一个 reorderTabs），焦点跟着它', async () => {
    mount()
    tab('c1').focus()
    key(tab('c1'), { key: 'ArrowRight', altKey: true })
    expect(useDocumentStore.getState().openTabs).toEqual(['c2', 'c1'])
    await flush()
    expect(document.activeElement).toBe(tab('c1'))
  })

  // Codex #833：改名框用 Enter / Esc 收起，焦点回到这个页签；点了别处（另一个输入框、工具条上的钮）收起的
  // 不抢回来——焦点已经在用户点的地方了，抢回页签会让接下来的打字 / 快捷键落进页签条
  describe('改名收起后的焦点', () => {
    const renameBox = () => host.querySelector<HTMLInputElement>('[data-canvas-tab-rename]')
    const startRename = () => {
      tab('c2').focus()
      key(tab('c2'), { key: 'F2' })
      expect(document.activeElement).toBe(renameBox())
    }

    it.each(['Enter', 'Escape'])('%s 收起改名：焦点回到这个页签', async (k) => {
      mount()
      startRename()
      key(renameBox()!, { key: k })
      await flush()
      expect(renameBox()).toBeNull()
      expect(document.activeElement).toBe(tab('c2'))
    })

    it('点别处收起改名：焦点留在用户点的那个输入框上', async () => {
      const other = document.createElement('input')
      document.body.appendChild(other)
      try {
        mount()
        startRename()
        act(() => other.focus())
        await flush()
        expect(renameBox()).toBeNull()
        expect(document.activeElement).toBe(other)
      } finally {
        other.remove()
      }
    })
  })

  // Codex #833：关掉第一个 / 中间那个页签后，焦点落到留下来的邻居上（右边那个，没有就左边那个），
  // 不按关之前的下标去取——那样会取回刚关掉的 id，焦点掉出页签条
  describe('Delete / ⌘W 关页签后焦点留在页签条里', () => {
    beforeEach(() => {
      const st = useDocumentStore.getState()
      const c3 = { ...st.canvases[0], id: 'c3', name: 'Figure 3' }
      useDocumentStore.setState({ canvases: [...st.canvases, c3], openTabs: ['c1', 'c2', 'c3'] })
    })

    it.each([
      ['第一个（Delete）', 'c1', { key: 'Delete' }, ['c2', 'c3'], 'c2'],
      ['中间那个（Delete）', 'c2', { key: 'Delete' }, ['c1', 'c3'], 'c3'],
      ['中间那个（⌘W）', 'c2', { key: 'w', metaKey: true }, ['c1', 'c3'], 'c3'],
      ['最后一个（Delete）', 'c3', { key: 'Delete' }, ['c1', 'c2'], 'c2'],
    ] as const)('%s → 焦点到 %s 的邻居', async (_name, closing, init, rest, focused) => {
      mount()
      tab(closing).focus()
      key(tab(closing), init)
      expect(useDocumentStore.getState().openTabs).toEqual(rest)
      await flush()
      expect(document.activeElement).toBe(tab(focused))
      expect(tab(focused).tabIndex).toBe(0)
    })
  })
})
