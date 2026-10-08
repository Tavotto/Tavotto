/**
 * 崩溃页（2026-10-07 设计审计 §10.2）：此前没有主按钮、「从空白开始」与「重新加载」同权重。
 * 现在一颗主按钮「重新加载」，「打开空白排版」在左边、危险浅底胶囊；报错原文收在「详情」里、能复制。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '@/components/ErrorBoundary'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function Boom(): never {
  throw new Error('kaboom')
}

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

describe('ErrorBoundary 崩溃页', () => {
  it('一颗主按钮「重新加载」；「打开空白排版」是左边的危险浅底胶囊；原文在折叠的详情里、可复制', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    act(() =>
      root.render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    )
    const screen = document.querySelector('[data-crash-screen]')!
    expect(screen).not.toBeNull()
    const primaries = screen.querySelectorAll('[data-variant="primary"]')
    expect(primaries).toHaveLength(1)
    expect(primaries[0].hasAttribute('data-crash-reload')).toBe(true)
    expect(screen.querySelector('[data-crash-blank]')!.getAttribute('data-variant')).toBe('danger-tinted')
    const details = screen.querySelector('details[data-crash-details]') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('kaboom')
    await act(async () => (screen.querySelector('[data-crash-copy]') as HTMLButtonElement).click())
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('kaboom'))
  })
})
