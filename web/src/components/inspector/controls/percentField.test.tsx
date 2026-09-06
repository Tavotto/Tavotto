/**
 * 透明度按百分比显示与输入（审计 T16 / T20）：显示 75%、输入 40 写回 0.4；
 * 写回值两位小数取整，不把 0.7000000000000001 落进 override。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fromPercent, PercentField, toPercent } from './PercentField'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement

async function mount(ui: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(ui)
  })
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  root = null
  document.body.innerHTML = ''
})

async function typeAndEnter(input: HTMLInputElement, text: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}

describe('PercentField', () => {
  it('换算：0–1 ↔ 百分比整数，写回按两位小数取整', () => {
    expect(toPercent(0.75)).toBe(75)
    expect(toPercent(1)).toBe(100)
    expect(toPercent('0.333')).toBe(33)
    expect(toPercent(undefined)).toBe(0)
    expect(fromPercent(40)).toBe(0.4)
    expect(fromPercent(33)).toBe(0.33)
    // 70 / 100 在浮点里就是 0.7，不会出现 0.7000000000000001
    expect(fromPercent(70)).toBe(0.7)
  })

  it('显示 75 与 %，输入 40 写回 0.4', async () => {
    const onChange = vi.fn()
    await mount(<PercentField value={0.75} ariaLabel="透明度" onChange={onChange} />)
    const input = host.querySelector<HTMLInputElement>('input[aria-label="透明度"]')!
    expect(input.value).toBe('75')
    expect(host.textContent).toContain('%')
    await typeAndEnter(input, '40')
    expect(onChange).toHaveBeenCalledWith(0.4)
  })

  it('越界按百分比夹到 0–100，再换回 0–1', async () => {
    const onChange = vi.fn()
    await mount(<PercentField value={0.5} ariaLabel="透明度" onChange={onChange} />)
    const input = host.querySelector<HTMLInputElement>('input[aria-label="透明度"]')!
    await typeAndEnter(input, '250')
    expect(onChange).toHaveBeenCalledWith(1)
  })

  it('多个值：不谎报其中一个数', async () => {
    await mount(<PercentField value={0.5} mixed ariaLabel="透明度" onChange={() => {}} />)
    const input = host.querySelector<HTMLInputElement>('input[aria-label="透明度"]')!
    expect(input.value).toBe('')
    expect(input.placeholder).not.toBe('')
  })
})
