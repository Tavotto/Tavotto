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

/**
 * 「多个值」（2026-10-07 审计 §9.2 P0）：多选颜色不一致时，色块不画任何一个颜色
 * ——之前批量行画 #000000、标注画 #1B1B18，再在旁边补一句「多个值」，控件本身在谎报。
 */
describe('ColorField 的「多个值」', () => {
  it('mixed：中性色块，不画 value 那个色，title 与可达描述都是「多个值」', async () => {
    await act(async () => {
      root.render(<ColorField value="#ff00ff" mixed onChange={() => {}} ariaLabel="边色" />)
    })
    const swatch = host.querySelector('[data-mixed]') as HTMLElement
    expect(swatch).not.toBeNull()
    expect(swatch.title).toBe('多个值')
    const fills = Array.from(swatch.querySelectorAll('div')).map((d) => d.style.background)
    expect(fills).not.toContain('rgb(255, 0, 255)')
    expect(fills).not.toContain('rgb(0, 0, 0)')
    const input = host.querySelector('input[type=color]') as HTMLInputElement
    const desc = document.getElementById(input.getAttribute('aria-describedby') ?? '')
    expect(desc?.textContent).toBe('多个值')
    // value 只当取色盘的起点
    expect(input.value).toBe('#ff00ff')
  })

  it('mixed 盖过「无」：起点是 none 时也画「多个值」，取色盘拿到合法色号', async () => {
    await act(async () => {
      root.render(<ColorField value={NO_COLOR} mixed onChange={() => {}} ariaLabel="边色" />)
    })
    expect(host.querySelector('[data-mixed]')).not.toBeNull()
    expect(host.querySelector('[data-none]')).toBeNull()
    expect((host.querySelector('input[type=color]') as HTMLInputElement).value).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('mixed 时取色照旧发出取色盘的色号', async () => {
    const onChange = vi.fn()
    await act(async () => {
      root.render(<ColorField value="#ff00ff" mixed onChange={onChange} ariaLabel="边色" />)
    })
    const input = host.querySelector('input[type=color]') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, '#123456')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(onChange).toHaveBeenCalledWith('#123456')
  })

  it('不 mixed 时没有描述、没有 data-mixed（对照组）', async () => {
    await act(async () => {
      root.render(<ColorField value="#ff00ff" onChange={() => {}} ariaLabel="边色" />)
    })
    expect(host.querySelector('[data-mixed]')).toBeNull()
    expect(host.querySelector('input[type=color]')!.hasAttribute('aria-describedby')).toBe(false)
  })
})

describe('最近用过的颜色（Codex #829 P2）', () => {
  it('系统取色器只发原生 change、从未聚焦也没有 blur：选定的颜色照样记进「最近」', async () => {
    const { resetRecentColors, useRecentColors } = await import('./colorPalette')
    resetRecentColors()
    let recent: readonly string[] = []
    function Probe() {
      recent = useRecentColors()
      return null
    }
    await act(async () =>
      root.render(
        <>
          <ColorField value="#000000" onChange={() => {}} ariaLabel="颜色" />
          <Probe />
        </>,
      ),
    )
    const native = host.querySelector<HTMLInputElement>('input[type="color"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(native, '#12ab34')
      native.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(document.activeElement).not.toBe(native)
    expect(recent.map((c) => c.toLowerCase())).toContain('#12ab34')
    resetRecentColors()
  })
})
