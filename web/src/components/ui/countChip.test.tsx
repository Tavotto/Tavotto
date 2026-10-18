/**
 * 计数芯片（ui/CountChip）：颜色只在圆点上，数字 tabular-nums，有 onClick 才是按钮。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CountChip } from './CountChip'

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
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

it('圆点是唯一的颜色：danger / warn 的圆点染色，quiet 灰；字与底从不染色', () => {
  act(() =>
    root.render(
      <>
        <CountChip tone="danger" count={5} label="项要改" />
        <CountChip tone="warn" count={4} label="项留意" />
        <CountChip tone="quiet" count={3} label="条建议" />
      </>,
    ),
  )
  const chips = [...host.querySelectorAll<HTMLElement>('[data-count-chip]')]
  expect(chips.map((c) => c.textContent)).toEqual(['5 项要改', '4 项留意', '3 条建议'])
  expect(chips.map((c) => c.querySelector('[aria-hidden]')!.className)).toEqual([
    expect.stringContaining('bg-danger'),
    expect.stringContaining('bg-warn'),
    expect.stringContaining('bg-ink-faint'),
  ])
  for (const c of chips) {
    expect(c.className).not.toMatch(/text-(danger|warn)|bg-(danger|warn)/)
    expect(c.className).toContain('rounded-full')
    expect(c.className).toContain('h-6.5')
    expect(c.querySelector('.tabular-nums')!.textContent).toMatch(/^\d+$/)
  }
  // 没有 onClick = 只读，不是按钮
  expect(host.querySelector('button')).toBeNull()
})

it('有 onClick 是按钮；pressed 给 aria-pressed；hideLabel 把文字留给读屏；lead 换掉圆点', () => {
  const onClick = vi.fn()
  act(() =>
    root.render(
      <CountChip count={2} label="警告" pressed onClick={onClick} hideLabel lead={<i data-lead />} aria-label="警告 2 项" />,
    ),
  )
  const b = host.querySelector<HTMLButtonElement>('button[data-count-chip]')!
  expect(b.getAttribute('aria-pressed')).toBe('true')
  expect(b.querySelector('[data-lead]')).not.toBeNull()
  expect(b.querySelector('.sr-only')!.textContent).toBe('警告')
  act(() => b.click())
  expect(onClick).toHaveBeenCalledTimes(1)
})
