/**
 * 铭牌 CSS 回退版刻名：渲染后实测，放不下就缩（不 overflow:hidden 裁字）；字体到位 / 容器变化时重算。
 * jsdom 没有排版，这里把 scrollWidth / clientWidth 换成「内容宽随 --fp-name-fit 缩放」的假实现来驱动。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FoundingPlate } from './FoundingPlate'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let natural = 300 // 不缩时名字的内容宽
const CONTAINER = 200

beforeEach(() => {
  document.body.innerHTML = ''
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('fp-name') ? CONTAINER : 0
  })
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function (this: HTMLElement) {
    if (!this.classList.contains('fp-name')) return 0
    const k = Number(this.style.getPropertyValue('--fp-name-fit') || 1)
    return natural * k
  })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

async function render(name: string) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<FoundingPlate no={37} since={1_763_000_000} name={name} onName={() => {}} />))
}
const nameEl = () => document.querySelector<HTMLElement>('[data-founding-shown]')!

describe('铭牌刻名的实测缩放', () => {
  it('实测超宽（宽字体回退）：按比例缩小到刚好放下，名字完整保留', async () => {
    natural = 300
    await render('W'.repeat(24))
    const k = Number(nameEl().style.getPropertyValue('--fp-name-fit'))
    expect(k).toBeGreaterThan(0.3)
    expect(k).toBeLessThan(0.7)
    expect(natural * k).toBeLessThanOrEqual(CONTAINER)
    expect(nameEl().textContent).toBe('W'.repeat(24)) // 一个字都没少
  })

  it('实测放得下：不写缩放', async () => {
    natural = 150
    await render('林小雨')
    expect(nameEl().style.getPropertyValue('--fp-name-fit')).toBe('')
  })
})
