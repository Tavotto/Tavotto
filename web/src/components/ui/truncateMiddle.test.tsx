/**
 * ui/TruncateMiddle 的按宽度测量模式：中间省略不留空隙、保留头尾；读屏拿到的永远是全名。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TruncateMiddle } from './TruncateMiddle'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
  // 每个字符 10px；盒宽 100px
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    font: '',
    measureText: (t: string) => ({ width: t.length * 10 }),
  } as never)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('measure：放不下时头部 + … + 尾部刚好放进盒宽，可见段对读屏隐藏，全名在 sr-only 里', () => {
  const full = 'Fig2_comparison_of_conditions'
  act(() => root.render(<TruncateMiddle text={full} tail={5} measure />))
  const visible = host.querySelector('[aria-hidden="true"]')!
  expect(visible.textContent!.length * 10).toBeLessThanOrEqual(100)
  expect(visible.textContent).toMatch(/^Fig2…tions$/)
  expect(host.querySelector('.sr-only')!.textContent).toBe(full)
})

it('measure：放得下就原样显示，不带省略号', () => {
  act(() => root.render(<TruncateMiddle text="Fig1" tail={5} measure />))
  expect(host.querySelector('[aria-hidden="true"]')!.textContent).toBe('Fig1')
})

it('没有 measure 时走 CSS 两段式（不带 aria-hidden / sr-only）', () => {
  act(() => root.render(<TruncateMiddle text="Fig2_comparison_of_conditions" tail={5} />))
  expect(host.querySelector('.sr-only')).toBeNull()
  expect(host.textContent).toBe('Fig2_comparison_of_conditions')
})
