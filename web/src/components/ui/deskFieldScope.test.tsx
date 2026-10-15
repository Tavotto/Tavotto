/**
 * 桌面子树的 field 重定向（`DESK_FIELD_SCOPE`）是不是**真的挂到了 DOM 根上**（Codex P1 #1099）。
 *
 * 此前这条判据是对源文件整体 `toContain` 字面量——注释或死代码里留着那串字也能通过。现在渲染三个消费方
 * （桌面 LeftPanel 停靠态、/try 的 DrawerShell、DESK_DRAWER 本身），断言**实际 className** 含常量里的每个 token；
 * 覆盖式抽屉（白浮层）不属于桌面子树，不该带。浏览器层的第二道是 e2e/a11y.spec 的 indistinctFieldBoxes（静态 + hover）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DESK_DRAWER } from '@/embedded/WidgetHeader'
import { DrawerShell } from '@/playground/components/DrawerShell'
import { LeftPanel } from '@/components/left/LeftPanel'
import { DESK_FIELD_SCOPE } from './fieldBox'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// 默认左抽屉是素材库，它量列宽用 ResizeObserver（jsdom 没有）
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

let container: HTMLDivElement
let root: Root
beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const tokens = DESK_FIELD_SCOPE.split(' ')
const hasScope = (el: Element) => tokens.every((t) => el.className.split(/\s+/).includes(t))

it('常量就是两个 token 改写', () => {
  expect(tokens).toHaveLength(2)
  expect(DESK_DRAWER.split(/\s+/)).toEqual(expect.arrayContaining(tokens))
})

it('桌面 LeftPanel 停靠态：aside 的实际 class 含 DESK_FIELD_SCOPE；覆盖式不含', async () => {
  await act(async () =>
    root.render(
      <TooltipProvider>
        <LeftPanel />
      </TooltipProvider>,
    ),
  )
  expect(hasScope(container.querySelector('aside[data-left-drawer]')!)).toBe(true)
  await act(async () =>
    root.render(
      <TooltipProvider>
        <LeftPanel overlay />
      </TooltipProvider>,
    ),
  )
  expect(hasScope(container.querySelector('aside[data-left-drawer]')!)).toBe(false)
})

it('/try 的 DrawerShell 经 DESK_DRAWER 带上 DESK_FIELD_SCOPE', () => {
  act(() =>
    root.render(
      <DrawerShell title="图内元素" count={1} countLabel="1">
        <div />
      </DrawerShell>,
    ),
  )
  expect(hasScope(container.querySelector('aside[data-drawer-shell]')!)).toBe(true)
})
