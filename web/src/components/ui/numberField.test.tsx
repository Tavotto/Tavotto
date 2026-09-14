/**
 * NumberField 的键盘步进（2026-09-14 审计 S13）：修饰键与拖动改数同一张表——
 * Shift ×10、Alt ×0.1。此前键盘只有 Shift，Alt 只在拖动时生效。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NumberField } from './Input'

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

const key = (el: Element, init: KeyboardEventInit) =>
  act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }))
  })

describe('NumberField 方向键步进', () => {
  it('↑ = step，Shift+↑ = 10×，Alt+↑ = 0.1×；↓ 反向', async () => {
    const onChange = vi.fn()
    await act(async () => {
      root.render(<NumberField value={10} step={1} onChange={onChange} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    await key(input, { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith(11)
    await key(input, { key: 'ArrowUp', shiftKey: true })
    expect(onChange).toHaveBeenLastCalledWith(20)
    await key(input, { key: 'ArrowUp', altKey: true })
    expect(onChange).toHaveBeenLastCalledWith(10.1)
    await key(input, { key: 'ArrowDown', altKey: true })
    expect(onChange).toHaveBeenLastCalledWith(9.9)
  })
})

describe('NumberField 钳位反馈（2026-09-14 二审 E6）', () => {
  it('提交的值被钳到上下界时框标记 data-clamped，界内提交不标记', async () => {
    const onChange = vi.fn()
    await act(async () => {
      root.render(<NumberField value={10} step={1} min={0} max={12} onChange={onChange} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    const box = () => host.querySelector('[data-clamped]')
    // 界内：↑ 到 11，不标记
    await key(input, { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith(11)
    expect(box()).toBeNull()
    // 越界：Shift+↑ 想加 10，被钳到 12，标记出现
    await key(input, { key: 'ArrowUp', shiftKey: true })
    expect(onChange).toHaveBeenLastCalledWith(12)
    expect(box(), '钳位那一刻框该亮一下').not.toBeNull()
  })
})
