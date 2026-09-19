/**
 * `ColorField` 的「无」状态（#427）：引擎报 `none` = 这条没有颜色（没设边色的形状、
 * `fill` 关着的面、空心 marker）。之前 `to_hex` 丢掉 alpha，透明黑显示成 #000000——
 * 检查器摆出一条并不存在的黑边。色块要画成「无」而不是黑色；取色盘只吃合法色号，
 * 喂它黑色当起点，用户一取色就得到一个真的颜色。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ColorField, NO_COLOR } from './Input'

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

describe('ColorField 的「无」', () => {
  it('none 画成「无」色块：不是黑色，title 说「无」，取色盘拿到的是合法色号', async () => {
    await act(async () => {
      root.render(<ColorField value={NO_COLOR} onChange={() => {}} ariaLabel="边色" />)
    })
    const swatch = host.querySelector('[data-none]') as HTMLElement
    expect(swatch).not.toBeNull()
    expect(swatch.title).not.toMatch(/^#/)
    expect(swatch.title).not.toBe('NONE')
    // 色块里没有一层 background 是 none / 黑
    const fills = Array.from(swatch.querySelectorAll('div')).map((d) => d.style.background)
    expect(fills).not.toContain('none')
    expect(fills).not.toContain('rgb(0, 0, 0)')
    const input = host.querySelector('input[type=color]') as HTMLInputElement
    expect(input.value).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('真颜色照旧：色块就是那个色，title 是色号', async () => {
    await act(async () => {
      root.render(<ColorField value="#ff00ff" onChange={() => {}} ariaLabel="边色" />)
    })
    expect(host.querySelector('[data-none]')).toBeNull()
    const input = host.querySelector('input[type=color]') as HTMLInputElement
    expect(input.value).toBe('#ff00ff')
    expect((input.parentElement as HTMLElement).title).toBe('#FF00FF')
  })

  it('从「无」取色：发出去的是取色盘的色号，不是 none', async () => {
    const onChange = vi.fn()
    await act(async () => {
      root.render(<ColorField value={NO_COLOR} onChange={onChange} ariaLabel="边色" />)
    })
    const input = host.querySelector('input[type=color]') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, '#123456')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(onChange).toHaveBeenCalledWith('#123456')
  })
})
