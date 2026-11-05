/**
 * Dialog 的 Option/Alt+Tab 边界循环：Radix FocusScope 只循环不带 Alt 的 Tab，
 * WebKit 靠 Option+Tab 遍历控件，焦点在首尾时要由外壳自己接手。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Dialog } from './Dialog'
import { TooltipProvider } from './Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const TITLE = 'T'

async function open() {
  await act(async () =>
    root.render(
      <TooltipProvider>
        <Dialog open onOpenChange={() => {}} title={TITLE} anchor="alt" size="sm">
          <button data-b="1">a</button>
          <button data-b="2">b</button>
          <button data-b="3">c</button>
          <button data-b="off" disabled>
            d
          </button>
          <button data-b="skip" tabIndex={-1}>
            e
          </button>
        </Dialog>
      </TooltipProvider>,
    ),
  )
  const q = (n: string) => document.querySelector<HTMLElement>(`[data-b="${n}"]`)!
  const content = document.querySelector<HTMLElement>('[data-dialog="alt"]')!
  // 外壳自带的右上角关闭钮排在正文之后，是 Tab 序列的最后一个
  const close = Array.from(content.querySelectorAll<HTMLElement>('button')).at(-1)!
  expect(close.dataset.b).toBeUndefined()
  return { q, content, close }
}

const press = (el: HTMLElement, init: KeyboardEventInit) => {
  const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true, ...init })
  act(() => {
    el.dispatchEvent(ev)
  })
  return ev
}

describe('Dialog：Alt+Tab 边界循环', () => {
  it('焦点在最后一个控件上按 Alt+Tab → 跳到第一个，并 preventDefault', async () => {
    const { q, close } = await open()
    close.focus()
    const ev = press(close, { altKey: true })
    expect(ev.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(q('1'))
  })

  it('焦点在第一个控件上按 Shift+Alt+Tab → 跳到最后一个（关闭钮；中间跳过禁用与 tabindex=-1）', async () => {
    const { q, close } = await open()
    q('1').focus()
    const ev = press(q('1'), { altKey: true, shiftKey: true })
    expect(ev.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(close)
  })

  it('不在边界：不拦，交给浏览器', async () => {
    const { q } = await open()
    q('2').focus()
    const fwd = press(q('2'), { altKey: true })
    expect(fwd.defaultPrevented).toBe(false)
    expect(document.activeElement).toBe(q('2'))
    const back = press(q('2'), { altKey: true, shiftKey: true })
    expect(back.defaultPrevented).toBe(false)
  })

  it('不带 Alt 的 Tab、带 Ctrl/Meta 的 Alt+Tab 都不接手', async () => {
    const { q } = await open()
    q('3').focus()
    expect(press(q('3'), {}).defaultPrevented).toBe(false)
    expect(press(q('3'), { altKey: true, ctrlKey: true }).defaultPrevented).toBe(false)
    expect(press(q('3'), { altKey: true, metaKey: true }).defaultPrevented).toBe(false)
    expect(document.activeElement).toBe(q('3'))
  })
})
