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

describe('NumberField onClear：清空框 = 回到「没设置」（设置 › 样式页，Codex #703）', () => {
  const type = (el: HTMLInputElement, v: string) =>
    act(async () => {
      el.focus()
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })

  it('有值时清空再回车：交 onClear，不交 onChange(0)', async () => {
    const onChange = vi.fn()
    const onClear = vi.fn()
    await act(async () => {
      root.render(<NumberField value={9} onChange={onChange} onClear={onClear} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    await type(input, '')
    await key(input, { key: 'Enter' })
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('清空后失焦同样交 onClear', async () => {
    const onClear = vi.fn()
    await act(async () => {
      root.render(<NumberField value={9} onChange={() => {}} onClear={onClear} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    await type(input, '')
    await act(async () => input.blur())
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('空框（mixed：样式里写着认不出的数）里按 Backspace 交 onClear；Tab 路过不交', async () => {
    const onClear = vi.fn()
    await act(async () => {
      root.render(<NumberField value={0} mixed mixedPlaceholder='"large"' onChange={() => {}} onClear={onClear} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    expect(input.placeholder).toBe('"large"')
    await act(async () => input.focus())
    await act(async () => input.blur())
    expect(onClear).not.toHaveBeenCalled()
    await act(async () => input.focus())
    await key(input, { key: 'Backspace' })
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('没给 onClear（属性页）：清空照旧还原，不提交', async () => {
    const onChange = vi.fn()
    await act(async () => {
      root.render(<NumberField value={9} onChange={onChange} ariaLabel="x" />)
    })
    const input = host.querySelector('input')!
    await type(input, '')
    await key(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('9')
  })
})
