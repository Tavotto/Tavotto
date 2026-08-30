/**
 * 展开 / 折叠左抽屉之后画布视口的重算（Prompt 08「常驻左侧工作区外壳」）。
 *
 * 停靠态的抽屉是画布的**兄弟 flex 项**：它一开一合，画布那个盒子当场变宽变窄。
 * 视口尺寸没跟上的话，命中测试、框选、吸附候选全部按旧的盒子算——用户点在图上，
 * 选中的却是旁边那一个。
 *
 * 「不造成对象跳动」的判据说的是**文档坐标**：视口变了只该改 `viewW/viewH/
 * originX/originY`，`zoom` / `panX` / `panY` 一位都不许动。动了的话画布上每个
 * 对象都会平移或缩放一下，而用户只是开了个抽屉。
 *
 * jsdom 没有布局引擎，所以「盒子真的变了」这一步由这里手工驱动
 * （`ResizeObserver` 的回调 + `getBoundingClientRect`），真实浏览器里的那一半
 * 由 `e2e/ux-consistency.spec.ts` 那条线覆盖。这里守的是**收到尺寸变化之后
 * store 变成什么样**——那正是缺陷会藏的地方。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { CanvasStage } from './CanvasStage'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 记录下每个被观察的节点与回调，好在用例里手工"发生一次尺寸变化" */
const observed: { el: Element; fire: () => void }[] = []
class RecordingResizeObserver {
  cb: () => void
  constructor(cb: () => void) {
    this.cb = cb
  }
  observe(el: Element) {
    observed.push({ el, fire: () => this.cb() })
  }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = RecordingResizeObserver as unknown as typeof ResizeObserver

/** 画布外框此刻有多宽——抽屉一开一合，真实浏览器里变的就是它 */
let stageWidth = 1000
const RECT = () =>
  ({ left: 44, top: 76, width: stageWidth, height: 600, right: 44 + stageWidth,
     bottom: 676, x: 44, y: 76, toJSON: () => ({}) }) as DOMRect

let container: HTMLDivElement
let root: Root
const realRect = Element.prototype.getBoundingClientRect

beforeEach(() => {
  localStorage.clear()
  observed.length = 0
  stageWidth = 1000
  // jsdom 的 getBoundingClientRect 恒为 0：换成一个跟着 stageWidth 走的
  Element.prototype.getBoundingClientRect = RECT as unknown as () => DOMRect
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<CanvasStage />))
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  Element.prototype.getBoundingClientRect = realRect
})

describe('抽屉开合 → 画布视口', () => {
  it('画布确实在盯着自己的盒子（抽屉一动，尺寸变化才到得了这里）', () => {
    expect(observed.length).toBeGreaterThan(0)
  })

  it('盒子变宽 → viewW 跟着变', () => {
    act(() => {
      useUiStore.getState().toggleLeft() // 收起抽屉，画布变宽
      stageWidth = 1300
      for (const o of observed) o.fire()
    })
    expect(useViewportStore.getState().viewW).toBe(1300)
  })

  it('盒子变窄 → viewW 与原点都跟着变', () => {
    act(() => {
      stageWidth = 700
      for (const o of observed) o.fire()
    })
    const vp = useViewportStore.getState()
    expect(vp.viewW).toBe(700)
    expect(vp.originX).toBe(44)
  })

  it('对象不跳动：zoom / panX / panY 一位都没动', () => {
    act(() => {
      useViewportStore.setState({ zoom: 1.75, panX: -120, panY: 33 })
    })
    act(() => {
      stageWidth = 1300
      for (const o of observed) o.fire()
    })
    const vp = useViewportStore.getState()
    // toBe 不是 toBeCloseTo：这里要的是"一位都没动"，不是"差不多"
    expect(vp.zoom).toBe(1.75)
    expect(vp.panX).toBe(-120)
    expect(vp.panY).toBe(33)
  })

  it('尺寸没变的一次通知不产生任何 store 更新（无差异 = 零 set）', () => {
    act(() => {
      stageWidth = 900
      for (const o of observed) o.fire()
    })
    let sets = 0
    const stop = useViewportStore.subscribe(() => {
      sets += 1
    })
    act(() => {
      for (const o of observed) o.fire() // 同一个盒子再通知一次
    })
    stop()
    expect(sets).toBe(0)
  })
})
