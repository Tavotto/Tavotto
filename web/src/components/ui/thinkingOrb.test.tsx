/**
 * 思考小球（ADR 0165）：几何、画笔与组件的循环。
 *
 * - 六种形态 × 两档尺寸都画得出点，点都落在画布里，16px 的点比 64px 少（派生档没把点数放大）；
 * - 画笔：深浅只用不透明度，小尺寸远点不低于 0.32；
 * - 组件：纯装饰（aria-hidden），tone 决定墨色 token；进行中一直重画、减少动态效果时只画一帧静止的；
 *   卸载后共享循环停掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThinkingOrb, type OrbForm } from './ThinkingOrb'
import { MODE_FRAMES } from './thinkingOrb/engine/registry'
import { alphaFloor, inkAlpha } from './thinkingOrb/paint'
import { resolvePreset } from './thinkingOrb/presets'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const FORMS: OrbForm[] = ['breathing', 'solving', 'composing', 'connecting', 'listening', 'searching']

describe('几何', () => {
  it.each(FORMS)('%s：16 / 64 都画得出点，点都在画布里，16px 的点更少', (form) => {
    const counts: number[] = []
    for (const size of [16, 64] as const) {
      const p = resolvePreset(form, size)
      for (const t of [0, 0.7, 3.1]) {
        const f = MODE_FRAMES[p.mode](size, t, p.opts)
        expect(f.dots.length).toBeGreaterThan(0)
        for (const d of f.dots) {
          expect(Number.isFinite(d.x) && Number.isFinite(d.y) && Number.isFinite(d.r)).toBe(true)
          expect(d.x).toBeGreaterThanOrEqual(-1)
          expect(d.x).toBeLessThanOrEqual(size + 1)
          expect(d.y).toBeGreaterThanOrEqual(-1)
          expect(d.y).toBeLessThanOrEqual(size + 1)
        }
        if (t === 0) counts.push(f.dots.length)
      }
    }
    expect(counts[0]).toBeLessThan(counts[1])
  })
})

describe('画笔', () => {
  it('最近的点不透明，最远的点停在下限；小尺寸下限更高', () => {
    expect(inkAlpha(0, undefined, alphaFloor(64))).toBeCloseTo(1)
    expect(inkAlpha(1, undefined, alphaFloor(64))).toBeCloseTo(0.18)
    expect(inkAlpha(1, undefined, alphaFloor(16))).toBeCloseTo(0.32)
    expect(inkAlpha(0, 0.5, alphaFloor(16))).toBeCloseTo(0.5)
  })
})

describe('组件', () => {
  let host: HTMLDivElement
  let root: Root
  let draws: number
  let realGetContext: typeof HTMLCanvasElement.prototype.getContext
  let realMatchMedia: typeof window.matchMedia

  const fakeCtx = () =>
    new Proxy(
      {},
      {
        get: (_t, k) => (k === 'clearRect' ? () => void draws++ : () => {}),
        set: () => true,
      },
    ) as unknown as CanvasRenderingContext2D

  const reduce = (on: boolean) => {
    window.matchMedia = ((q: string) => ({
      matches: on && q.includes('reduce'),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia
  }

  beforeEach(() => {
    draws = 0
    realGetContext = HTMLCanvasElement.prototype.getContext
    realMatchMedia = window.matchMedia
    HTMLCanvasElement.prototype.getContext = (() => fakeCtx()) as never
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })

  afterEach(() => {
    act(() => root.unmount())
    host.remove()
    HTMLCanvasElement.prototype.getContext = realGetContext
    window.matchMedia = realMatchMedia
    vi.useRealTimers()
  })

  it('纯装饰；进行中用 ink、结束后用 ink-3', () => {
    reduce(false)
    act(() =>
      root.render(
        <>
          <ThinkingOrb form="solving" />
          <ThinkingOrb form="listening" tone="settled" />
        </>,
      ),
    )
    const [a, b] = Array.from(host.querySelectorAll<HTMLCanvasElement>('canvas[data-thinking-orb]'))
    expect(a.getAttribute('aria-hidden')).toBe('true')
    expect(a.dataset.thinkingOrb).toBe('solving')
    expect(a.classList.contains('text-ink')).toBe(true)
    expect(b.dataset.thinkingOrbTone).toBe('settled')
    expect(b.classList.contains('text-ink-3')).toBe(true)
  })

  it('一直在重画（结束后的也是），卸载后停下', async () => {
    reduce(false)
    vi.useFakeTimers()
    act(() => root.render(<ThinkingOrb form="breathing" tone="settled" />))
    const first = draws
    await act(async () => vi.advanceTimersByTime(600))
    const later = draws
    expect(later).toBeGreaterThan(first)
    act(() => root.render(<></>))
    await act(async () => vi.advanceTimersByTime(600))
    expect(draws).toBe(later)
  })

  it('减少动态效果：只画一帧，不进循环', async () => {
    reduce(true)
    vi.useFakeTimers()
    act(() => root.render(<ThinkingOrb form="breathing" />))
    const first = draws
    expect(first).toBeGreaterThan(0)
    await act(async () => vi.advanceTimersByTime(600))
    // 换主题那次重读墨色允许多画一帧，但不会随时间继续画
    const settled = draws
    await act(async () => vi.advanceTimersByTime(600))
    expect(draws).toBe(settled)
    expect(settled - first).toBeLessThanOrEqual(1)
  })
})
