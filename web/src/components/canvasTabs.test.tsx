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

  it('条高 36：与右栏页签同档（此前 32）；页签填满条的内容盒，不另写 36', () => {
    mount()
    const strip = host.querySelector('[data-canvas-tabs]') as HTMLElement
    expect(strip.parentElement!.className, '条本身 36').toContain('h-9')
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
})
