/**
 * 非模态浮层的 Tab 焦点陷阱（快速编辑弹层用）。主语：派发 Tab 之后 `document.activeElement` 落在哪、
 * 事件有没有被拦（`defaultPrevented`）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { trapTab } from './focusTrap'

let box: HTMLDivElement
let a: HTMLButtonElement
let b: HTMLInputElement
let outside: HTMLButtonElement

beforeEach(() => {
  box = document.createElement('div')
  a = document.createElement('button')
  b = document.createElement('input')
  box.append(a, b)
  outside = document.createElement('button')
  document.body.append(box, outside)
})
afterEach(() => {
  document.body.innerHTML = ''
})

const tab = (shift = false) => {
  const e = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, cancelable: true })
  trapTab(e, box)
  return e
}

describe('trapTab', () => {
  it('最后一个上按 Tab：绕回第一个', () => {
    b.focus()
    expect(tab().defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(a)
  })

  it('第一个上按 Shift+Tab：绕到最后一个', () => {
    a.focus()
    expect(tab(true).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(b)
  })

  it('中间照常走（不拦）', () => {
    a.focus()
    expect(tab().defaultPrevented).toBe(false)
  })

  it('焦点不在里面：拉回第一个', () => {
    outside.focus()
    expect(tab().defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(a)
  })
})
