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

describe('NumberField 拖数字的提交与作废', () => {
  const start = async () => {
    const prefix = host.querySelector('[data-number-scrub]') as HTMLElement
    Object.assign(prefix, {
      setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn(),
    })
    await act(async () => prefix.dispatchEvent(new MouseEvent('pointerdown', { clientX: 0, button: 0, bubbles: true })))
  }
  const fire = (type: string, clientX = 10) => act(async () => {
    window.dispatchEvent(new MouseEvent(type, { clientX, bubbles: true }))
  })
  const mount = async (cancel = true) => {
    const callbacks = { onChange: vi.fn(), onScrubStart: vi.fn(), onScrubEnd: vi.fn(), onScrubCancel: vi.fn() }
    await act(async () => root.render(<NumberField value={10} prefix="W" prefixInside {...callbacks} onScrubCancel={cancel ? callbacks.onScrubCancel : undefined} />))
    await start()
    return callbacks
  }

  it('正常松手保留既有行为，一次 begin / end，结束后没有残留监听', async () => {
    const c = await mount()
    await fire('pointermove', 10)
    await fire('pointermove', 20)
    await fire('pointerup')
    await fire('lostpointercapture')
    await fire('pointermove', 30)
    expect(c.onScrubStart).toHaveBeenCalledTimes(1)
    expect(c.onChange.mock.calls).toEqual([[20], [30]])
    expect(c.onScrubEnd).toHaveBeenCalledTimes(1)
    expect(c.onScrubCancel).not.toHaveBeenCalled()
  })

  it.each(['pointercancel', 'lostpointercapture'])('%s 只调用取消出口，随后松手不提交', async (event) => {
    const c = await mount()
    await fire('pointermove')
    await fire(event)
    await fire('pointerup')
    await fire('pointermove', 50)
    expect(c.onScrubCancel).toHaveBeenCalledTimes(1)
    expect(c.onScrubEnd).not.toHaveBeenCalled()
    expect(c.onChange).toHaveBeenCalledTimes(1)
  })

  it('Esc 由有取消能力的控件接住，松手不再补提交', async () => {
    const c = await mount()
    await fire('pointermove')
    await key(window.document.body, { key: 'Escape' })
    await fire('pointerup')
    expect(c.onScrubCancel).toHaveBeenCalledTimes(1)
    expect(c.onScrubEnd).not.toHaveBeenCalled()
  })

  it('卸载会作废正在预览的数字手势并清监听', async () => {
    const c = await mount()
    await fire('pointermove')
    await act(async () => root.render(null))
    await fire('pointerup')
    await fire('pointermove', 50)
    expect(c.onScrubCancel).toHaveBeenCalledTimes(1)
    expect(c.onScrubEnd).not.toHaveBeenCalled()
    expect(c.onChange).toHaveBeenCalledTimes(1)
  })

  it('既有即时写入控件未给取消回调时仍收尾其事务', async () => {
    const c = await mount(false)
    await fire('pointermove')
    await fire('pointercancel')
    expect(c.onScrubEnd).toHaveBeenCalledTimes(1)
    expect(c.onScrubCancel).not.toHaveBeenCalled()
  })
})
