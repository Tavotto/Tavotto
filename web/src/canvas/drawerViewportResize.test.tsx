/**
 * 展开 / 折叠左抽屉之后画布视口的重算（Prompt 08「常驻左侧工作区外壳」）。
 *
 * 停靠态的抽屉是画布的**兄弟 flex 项**：它一开一合，画布那个盒子当场变宽变窄。
 * 视口尺寸没跟上的话，命中测试、框选、吸附候选全部按旧的盒子算——用户点在图上，
 * 选中的却是旁边那一个。
 *
 * 「不造成对象跳动」的判据说的是**文档坐标**：用户自己缩放 / 平移过之后，视口
 * 变了只该改 `viewW/viewH/originX/originY`，`zoom` / `panX` / `panY` 一位都不许动。
 * 动了的话画布上每个对象都会平移或缩放一下，而用户只是开了个抽屉。
 * 反过来，用户**还没动过**视口（仍在「适应画布」模式）时，抽屉开合要按新的盒子
 * 重算适配（审计 B01 / B57：首次打开的适配是在侧栏展开之前算的，不重算就装不下画布）。
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

  it('用户动过视口之后，对象不跳动：zoom / panX / panY 一位都没动', () => {
    act(() => {
      // 缩放值直接摆，平移走 store 的动作：用户的缩放 / 平移正是经这些动作退出适应模式的
      useViewportStore.setState({ zoom: 1.75 })
      useViewportStore.getState().setPan(-120, 33)
    })
    expect(useViewportStore.getState().fitted).toBe(false)
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

  it('还在适应模式（用户没动过）：抽屉开合按新盒子重算，页面仍整个在视野里', () => {
    // 挂载时按 1000 宽适配过一次（`CanvasStage` 首次拿到尺寸就 fit）
    const before = useViewportStore.getState()
    expect(before.fitted).toBe(true)
    act(() => {
      stageWidth = 700 // 抽屉展开，画布变窄
      for (const o of observed) o.fire()
    })
    const vp = useViewportStore.getState()
    expect(vp.fitted).toBe(true)
    expect(vp.zoom).toBeLessThan(before.zoom)
    // 页面右缘仍在视口内（150mm 默认页面）
    expect(vp.panX).toBeGreaterThanOrEqual(0)
    expect(vp.panX + 150 * (96 / 25.4) * vp.zoom).toBeLessThanOrEqual(700)
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
