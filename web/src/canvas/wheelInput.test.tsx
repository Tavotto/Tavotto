/**
 * 画布滚轮：deltaMode 归一化在真实事件链路上的落点。
 *
 * 背景：`onWheel` 原先直接把 `e.deltaY`/`e.deltaX` 当像素用。Chrome/Safari 恒发
 * deltaMode=0（一格 100–120），Firefox 桌面版默认发 deltaMode=1（一格 3 行），
 * 于是同一个滚轮在 Firefox 里只走 3px——缩放/平移慢几十倍
 * （docs/audit/2026-08-17-ux-audit.md medium 组「滚轮 deltaMode」）。
 *
 * 这里钉两件事：像素模式下的数值**逐位不变**（现有手感不能被这次改动碰到），
 * 以及行/页模式折算后与像素模式落在同一量级。真机 Firefox 的手感校准不在
 * 沙箱能力范围内，取值依据写在 `lib/wheel.ts` 头注释里。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { WHEEL_LINE_PX } from '@/lib/wheel'
import { useViewportStore } from '@/store/viewportStore'
import { CanvasStage } from './CanvasStage'

// jsdom 没有 ResizeObserver；这里只关心 wheel 监听器，视口尺寸手工塞
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

/** 缩放步长公式的唯一常量（CanvasStage 里的 0.0022），测试独立写一遍当对照 */
const ZOOM_K = 0.0022
const VIEW = { w: 800, h: 600 }

let container: HTMLDivElement
let root: Root

const fire = (init: WheelEventInit) => {
  const el = container.querySelector('[data-canvas-stage]')
  expect(el).toBeTruthy()
  act(() => {
    el!.dispatchEvent(
      new WheelEvent('wheel', {
        bubbles: true,
        cancelable: true,
        clientX: 400,
        clientY: 300,
        ...init,
      }),
    )
  })
}

const vp = () => useViewportStore.getState()

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<CanvasStage />))
  // 挂载后再塞视口：jsdom 的 getBoundingClientRect 全是 0，effect 里的 fit 不会触发
  useViewportStore.setState({
    zoom: 1,
    panX: 0,
    panY: 0,
    originX: 0,
    originY: 0,
    viewW: VIEW.w,
    viewH: VIEW.h,
  })
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('画布滚轮 deltaMode 归一化', () => {
  it('像素模式（Chrome/Safari）缩放逐位不变', () => {
    fire({ deltaY: -100, ctrlKey: true })
    // 归一化必须是恒等变换：这里是 toBe，不是 toBeCloseTo
    expect(vp().zoom).toBe(Math.exp(100 * ZOOM_K))
  })

  it('像素模式平移逐位不变', () => {
    fire({ deltaX: 40, deltaY: 100 })
    expect(vp().panX).toBe(-40)
    expect(vp().panY).toBe(-100)
  })

  it('触控板捏合（ctrlKey + 极小 deltaY，deltaMode 恒为 0）不受影响', () => {
    fire({ deltaY: -8, ctrlKey: true })
    expect(vp().zoom).toBe(Math.exp(8 * ZOOM_K))
    // 连续几帧小增量仍然连续累积，没有被行折算放大成跳变
    fire({ deltaY: -8, ctrlKey: true })
    expect(vp().zoom).toBeCloseTo(Math.exp(16 * ZOOM_K), 12)
  })

  it('行模式（Firefox 默认）按 WHEEL_LINE_PX 折算后平移', () => {
    fire({ deltaY: 3, deltaX: 1, deltaMode: 1 })
    expect(vp().panY).toBe(-3 * WHEEL_LINE_PX)
    expect(vp().panX).toBe(-WHEEL_LINE_PX)
  })

  it('行模式缩放按折算后的像素量走同一个步长公式', () => {
    fire({ deltaY: -3, ctrlKey: true, deltaMode: 1 })
    expect(vp().zoom).toBe(Math.exp(3 * WHEEL_LINE_PX * ZOOM_K))
  })

  it('页模式按视口高折算', () => {
    fire({ deltaY: 1, deltaMode: 2 })
    expect(vp().panY).toBe(-VIEW.h)
  })

  it('Chrome 一格与 Firefox 一格：平移/缩放都落在同一量级', () => {
    // Chrome 一格 ≈100px，Firefox 一格 =3 行
    fire({ deltaY: 100 })
    const chromePan = vp().panY
    useViewportStore.setState({ panY: 0 })
    fire({ deltaY: 3, deltaMode: 1 })
    const firefoxPan = vp().panY
    expect(firefoxPan / chromePan).toBeGreaterThan(0.5)
    expect(firefoxPan / chromePan).toBeLessThan(2)

    useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
    fire({ deltaY: -100, ctrlKey: true })
    const chromeZoom = vp().zoom - 1
    useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
    fire({ deltaY: -3, ctrlKey: true, deltaMode: 1 })
    const firefoxZoom = vp().zoom - 1
    expect(chromeZoom).toBeGreaterThan(0.2) // 一格约 1.25×，先确认对照组本身是有效的
    expect(firefoxZoom / chromeZoom).toBeGreaterThan(0.5)
    expect(firefoxZoom / chromeZoom).toBeLessThan(2)
  })

  it('反向验证：不归一化的话 Firefox 一格慢两个数量级', () => {
    // 这条钉的是「问题确实存在」——去掉归一化就等于 deltaY=3 当 3px 用
    fire({ deltaY: 3 })
    const raw = vp().panY
    useViewportStore.setState({ panY: 0 })
    fire({ deltaY: 3, deltaMode: 1 })
    expect(Math.abs(vp().panY / raw)).toBe(WHEEL_LINE_PX)
  })
})
