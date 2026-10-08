/**
 * /try 编辑态的元素抽屉（2026-10-07 设计审计 §10.4）：与桌面版左抽屉同一副骨架——宽 280（应用抽屉下限）、
 * 36px 标题行（标题 + 只有数字的计数，单位进读屏）、坐在 `--drawer-bg` 上、不画分隔线。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { LEFT_MIN } from '@/store/uiStore'
import { WidgetHeader } from '@/embedded/WidgetHeader'
import { DrawerShell } from './DrawerShell'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

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

it('宽 280、36px 标题行带计数、抽屉底是 --drawer-bg、没有边线', () => {
  act(() =>
    root.render(
      <DrawerShell title="图内元素" count={12} countLabel="12 个元素">
        <div data-tree />
      </DrawerShell>,
    ),
  )
  const aside = container.querySelector<HTMLElement>('aside[data-drawer-shell]')!
  expect(aside.style.width).toBe(`${LEFT_MIN}px`)
  expect(LEFT_MIN).toBe(280)
  expect(aside.className).toContain('[--drawer-bg:var(--color-bg)]')
  expect(aside.className).not.toMatch(/\bborder-[rl]\b/)
  const header = aside.firstElementChild as HTMLElement
  expect(header.className).toContain('h-9')
  expect(header.querySelector('h2')!.textContent).toBe('图内元素')
  expect(header.textContent).toContain('12')
  expect(aside.querySelector('[data-tree]')).not.toBeNull()
})

it('计数为 0 不显示', () => {
  act(() =>
    root.render(
      <DrawerShell title="图内元素" count={0}>
        <div />
      </DrawerShell>,
    ),
  )
  expect(container.querySelector('h2')!.parentElement!.textContent).toBe('图内元素')
})

it('WidgetHeader：44px、品牌标 + 名字 + type-meta 标题，右侧放调用方的控件', () => {
  act(() =>
    root.render(
      <WidgetHeader name="FigE2E" title="lab v1">
        <button type="button" data-right />
      </WidgetHeader>,
    ),
  )
  const header = container.querySelector<HTMLElement>('header[data-widget-header]')!
  expect(header.className).toContain('h-11')
  expect(header.querySelector('svg')).not.toBeNull()
  expect(header.textContent).toContain('FigE2E')
  expect(header.querySelector('.type-meta')!.textContent).toBe('lab v1')
  expect(header.querySelector('[data-right]')).not.toBeNull()
})
