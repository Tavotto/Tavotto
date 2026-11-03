import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { dotLayout, dotsFor } from '@/lib/dotProgress'
import { ProgressBar } from './ProgressBar'

/** 假 ResizeObserver：observe 时立刻报一个 devicePixelContentBoxSize（模拟 Windows 125%：336×12 CSS → 420×15 物理像素） */
const DEV = { w: 420, h: 15, css: 336 }
class FakeRO {
  private cb: ResizeObserverCallback
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
  }
  observe(el: Element) {
    const entry = {
      target: el,
      contentRect: { width: DEV.css, height: 12 },
      devicePixelContentBoxSize: [{ inlineSize: DEV.w, blockSize: DEV.h }],
    } as unknown as ResizeObserverEntry
    this.cb([entry], this as unknown as ResizeObserver)
  }
  disconnect() {}
  unobserve() {}
}

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
const render = (node: ReactNode) => {
  act(() => root.render(node))
  return { container: host, rerender: (n: ReactNode) => act(() => root.render(n)) }
}

let rects: [number, number, number, number][]
const origGetContext = HTMLCanvasElement.prototype.getContext
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  rects = []
  vi.stubGlobal('ResizeObserver', FakeRO)
  HTMLCanvasElement.prototype.getContext = (() => ({
    clearRect: () => {
      rects = []
    },
    fillRect: (x: number, y: number, w: number, h: number) => rects.push([x, y, w, h]),
    fillStyle: '',
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  HTMLCanvasElement.prototype.getContext = origGetContext
  vi.useRealTimers()
})

describe('ui/ProgressBar 点阵（ADR 0168）', () => {
  it('确定态：报 aria-valuenow；位图 = 实际物理像素；画的点 = 几何模块算出的点', () => {
    const { container } = render(<ProgressBar pct={64} label="下载" />)
    const bar = container.querySelector('[role="progressbar"]')!
    expect(bar.getAttribute('aria-valuenow')).toBe('64')
    expect(bar.hasAttribute('data-progress-indeterminate')).toBe(false)
    const canvas = bar.querySelector('canvas')!
    expect([canvas.width, canvas.height]).toEqual([DEV.w, DEV.h])
    const l = dotLayout(DEV.w, DEV.h, DEV.css)
    const want = dotsFor(l, 0, Math.round(0.64 * l.cells)).map(([x, y]) => [x, y, l.dot, l.dot])
    expect(rects).toEqual(want)
    // 125%：点是 1 个物理像素，不是 1.25
    expect(rects.every(([, , w, h]) => w === 1 && h === 1)).toBe(true)
  })

  it('不确定态：不报 aria-valuenow；一段密点来回扫（位置随时间变）', () => {
    vi.useFakeTimers()
    const { container } = render(<ProgressBar pct={null} label="导出" />)
    const bar = container.querySelector('[role="progressbar"]')!
    expect(bar.hasAttribute('aria-valuenow')).toBe(false)
    expect(bar.hasAttribute('data-progress-indeterminate')).toBe(true)
    // 第 2 行（y = 2 个点径）只有密点才有：那一段的最左 x 就是扫到的位置
    const l = dotLayout(DEV.w, DEV.h, DEV.css)
    const firstX = () => Math.min(...rects.filter(([, y]) => y === l.oy + l.filledStep).map(([x]) => x))
    expect(rects.length).toBeGreaterThan(0)
    const x0 = firstX()
    expect(Number.isFinite(x0)).toBe(true)
    act(() => {
      vi.advanceTimersByTime(80 * 5)
    })
    expect(firstX()).not.toBe(x0)
  })

  it('百分比变化时一格一格补齐，不是一下跳到位', () => {
    vi.useFakeTimers()
    const { rerender } = render(<ProgressBar pct={0} label="下载" />)
    const l = dotLayout(DEV.w, DEV.h, DEV.css)
    const denseCount = () => rects.length
    const empty = denseCount()
    rerender(<ProgressBar pct={100} label="下载" />)
    act(() => {
      vi.advanceTimersByTime(18 * 3)
    })
    const mid = denseCount()
    expect(mid).toBeGreaterThan(empty)
    expect(mid).toBeLessThan(dotsFor(l, 0, l.cells).length)
    // 一次推进很多拍（中间不重渲染）也要走满，不能卡在 +1
    act(() => {
      vi.advanceTimersByTime(18 * (l.cells + 5))
    })
    expect(denseCount()).toBe(dotsFor(l, 0, l.cells).length)
  })
})
