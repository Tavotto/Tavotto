/**
 * 共享滚动渐隐（ui/scrollFade）：量边界、mask 样式、hook 的监听与清理。
 * jsdom 没有布局引擎，几何要自己装值（defineProperty）；像素归真浏览器（e2e/canvas-tabs-scroll.spec.ts）。
 */
import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { measureScrollFade, scrollFadeStyle, useScrollFade, type ScrollAxis } from './scrollFade'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

function geometry(el: HTMLElement, g: { pos: number; view: number; total: number }, axis: ScrollAxis) {
  const [p, v, t] = axis === 'x' ? (['scrollLeft', 'clientWidth', 'scrollWidth'] as const) : (['scrollTop', 'clientHeight', 'scrollHeight'] as const)
  Object.defineProperty(el, p, { configurable: true, writable: true, value: g.pos })
  Object.defineProperty(el, v, { configurable: true, value: g.view })
  Object.defineProperty(el, t, { configurable: true, value: g.total })
}

describe('measureScrollFade', () => {
  it.each(['x', 'y'] as const)('%s 轴：顶端只有 end、中段两侧、末端只有 start、不溢出两侧都无（1px 容差）', (axis) => {
    const el = document.createElement('div')
    geometry(el, { pos: 0, view: 100, total: 300 }, axis)
    expect(measureScrollFade(el, axis)).toEqual({ start: false, end: true })
    geometry(el, { pos: 100, view: 100, total: 300 }, axis)
    expect(measureScrollFade(el, axis)).toEqual({ start: true, end: true })
    geometry(el, { pos: 200, view: 100, total: 300 }, axis)
    expect(measureScrollFade(el, axis)).toEqual({ start: true, end: false })
    geometry(el, { pos: 0, view: 100, total: 100 }, axis)
    expect(measureScrollFade(el, axis)).toEqual({ start: false, end: false })
    // 小数像素：最大 scrollLeft 向下取整，差不到 1px 不算还有内容
    geometry(el, { pos: 199.5, view: 100, total: 300 }, axis)
    expect(measureScrollFade(el, axis).end).toBe(false)
  })
})

describe('scrollFadeStyle', () => {
  it('两侧都不要 → undefined；只有 end → 尾部淡出；只有 start → 头部淡出；Webkit 前缀同值', () => {
    expect(scrollFadeStyle({ start: false, end: false }, 'x')).toBeUndefined()
    const end = scrollFadeStyle({ start: false, end: true }, 'x')!
    expect(end.maskImage).toBe('linear-gradient(to right, #000 0, #000 calc(100% - 16px), transparent 100%)')
    expect(end.WebkitMaskImage).toBe(end.maskImage)
    const start = scrollFadeStyle({ start: true, end: false }, 'x')!
    expect(start.maskImage).toBe('linear-gradient(to right, transparent 0, #000 16px, #000 100%)')
    const both = scrollFadeStyle({ start: true, end: true }, 'y', 12)!
    expect(both.maskImage).toBe(
      'linear-gradient(to bottom, transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%)',
    )
  })
})

describe('useScrollFade', () => {
  let root: Root
  let host: HTMLDivElement
  let observed: ResizeObserver[]
  const RealRO = globalThis.ResizeObserver

  beforeEach(() => {
    observed = []
    globalThis.ResizeObserver = class {
      disconnect = vi.fn()
      observe = vi.fn()
      unobserve = vi.fn()
      constructor() {
        observed.push(this as unknown as ResizeObserver)
      }
    } as unknown as typeof ResizeObserver
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    host.remove()
    globalThis.ResizeObserver = RealRO
    vi.restoreAllMocks()
  })

  function Probe({ axis }: { axis: ScrollAxis }) {
    const ref = useRef<HTMLDivElement>(null)
    const { fade } = useScrollFade(ref, axis)
    return (
      <div
        ref={ref}
        data-probe
        data-fade-start={fade.start || undefined}
        data-fade-end={fade.end || undefined}
      />
    )
  }
  const probe = () => host.querySelector<HTMLElement>('[data-probe]')!

  it('溢出时画 end；滚到中段两侧、滚到头 end 消失；监听是 passive 的', async () => {
    const add = vi.spyOn(HTMLElement.prototype, 'addEventListener')
    const proto = HTMLElement.prototype
    // 在挂载前给原型装几何，让首次量就读到溢出
    Object.defineProperty(proto, 'clientWidth', { configurable: true, value: 100 })
    Object.defineProperty(proto, 'scrollWidth', { configurable: true, value: 300 })
    Object.defineProperty(proto, 'scrollLeft', { configurable: true, writable: true, value: 0 })
    try {
      await act(async () => root.render(<Probe axis="x" />))
      expect(probe().dataset.fadeEnd).toBe('true')
      expect(probe().dataset.fadeStart).toBeUndefined()
      const call = add.mock.calls.find(([type]) => type === 'scroll')!
      expect(call[2]).toEqual({ passive: true })

      await act(async () => {
        probe().scrollLeft = 100
        probe().dispatchEvent(new Event('scroll'))
      })
      expect(probe().dataset.fadeStart).toBe('true')
      expect(probe().dataset.fadeEnd).toBe('true')

      await act(async () => {
        probe().scrollLeft = 200
        probe().dispatchEvent(new Event('scroll'))
      })
      expect(probe().dataset.fadeStart).toBe('true')
      expect(probe().dataset.fadeEnd).toBeUndefined()
    } finally {
      // @ts-expect-error 还原：删掉测试装在原型上的几何
      delete proto.clientWidth
      // @ts-expect-error 同上
      delete proto.scrollWidth
      // @ts-expect-error 同上
      delete proto.scrollLeft
    }
  })

  it('卸载时摘掉 scroll 监听并断开 ResizeObserver（不泄漏）', async () => {
    const remove = vi.spyOn(HTMLElement.prototype, 'removeEventListener')
    await act(async () => root.render(<Probe axis="y" />))
    expect(observed).toHaveLength(1)
    const ro = observed[0] as unknown as { disconnect: ReturnType<typeof vi.fn> }
    expect(ro.disconnect).not.toHaveBeenCalled()
    await act(async () => root.render(<div />))
    expect(remove.mock.calls.some(([type]) => type === 'scroll')).toBe(true)
    expect(ro.disconnect).toHaveBeenCalledTimes(1)
  })
})
