/**
 * 属性栏渐变色带的手势生命周期 + 多选角度（Codex 线程 4614459810 / 4614459832）：
 * 拖色标登记进手势协调器（Esc = 放弃这一拖、保存前 = 收成一条历史、卸载不悬事务、pointercancel 放弃）；
 * 角度按每个对象自己的宽高换算。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { linearAngle } from '@/lib/gradient'
import { useDocumentStore } from '@/store/documentStore'
import {
  cancelActivePointerGesture,
  finishActiveGesture,
  hasActiveGesture,
  hasActivePointerGesture,
  resetGestureCoordinator,
} from '@/store/gestureCoordinator'
import { emptyProject, type Gradient, type ShapeObject } from '@/types/document'
import { GradientEditor, GradientTools } from './GradientControls'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const grad = (): Gradient => ({
  kind: 'linear',
  from: [0, 0.5],
  to: [1, 0.5],
  stops: [
    { offset: 0, color: '#ff0000' },
    { offset: 0.5, color: '#00ff00' },
    { offset: 1, color: '#0000ff' },
  ],
})
const box = (id: string, w: number, h: number): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 0,
  y: 0,
  w,
  h,
  strokePt: 0,
  strokeAlign: 'center',
  color: '#000000',
  fill: '#ff0000',
  fillGradient: grad(),
})
const objs = () => useDocumentStore.getState().doc.objects
const stops = () => (objs()[0] as ShapeObject).fillGradient!.stops.map((s) => s.offset)

let root: Root
let host: HTMLDivElement
async function seed(items: ShapeObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_gc_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}
const mount = async (ui: React.ReactNode) =>
  act(async () => {
    root.render(<TooltipProvider>{ui}</TooltipProvider>)
  })
const stop = (i: number) => host.querySelector<HTMLElement>(`[data-gradient-stop="${i}"]`)!
const ptr = (el: Element, type: string, x: number) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: 0, button: 0 }))
  })

beforeEach(() => {
  resetGestureCoordinator()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function dragStart() {
  await seed([box('a', 40, 20)])
  await mount(<GradientEditor objs={objs() as ShapeObject[]} target="fill" />)
  const bar = host.querySelector('[data-gradient-bar]')!
  bar.getBoundingClientRect = () => ({ left: 0, right: 100, width: 100, top: 0, bottom: 20, height: 20, x: 0, y: 0, toJSON() {} }) as DOMRect
  await ptr(stop(1), 'pointerdown', 50)
  await ptr(stop(1), 'pointermove', 80)
}

describe('渐变色标拖动', () => {
  it('Esc（指针取消出口）= 放弃这一拖：文档回原样、事务关掉、不进历史', async () => {
    await dragStart()
    expect(stops()[1]).toBeCloseTo(0.8)
    expect(hasActivePointerGesture()).toBe(true)
    expect(cancelActivePointerGesture()).toBe(true)
    expect(stops()).toEqual([0, 0.5, 1])
    expect(useDocumentStore.getState().txn).toBeNull()
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(hasActiveGesture()).toBe(false)
    expect(hasActivePointerGesture()).toBe(false)
  })

  it('保存前 finishActiveGesture() = 收成一条历史', async () => {
    await dragStart()
    finishActiveGesture()
    expect(useDocumentStore.getState().txn).toBeNull()
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(stops()[1]).toBeCloseTo(0.8)
  })

  it('pointercancel = 放弃，不提交', async () => {
    await dragStart()
    await ptr(stop(1), 'pointercancel', 80)
    expect(stops()).toEqual([0, 0.5, 1])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(hasActiveGesture()).toBe(false)
  })

  it('拖到一半卸载：事务不悬着、登记注销', async () => {
    await dragStart()
    await act(async () => root.render(<div />))
    expect(useDocumentStore.getState().txn).toBeNull()
    expect(hasActiveGesture()).toBe(false)
    expect(hasActivePointerGesture()).toBe(false)
  })
})

describe('多选角度', () => {
  it('宽高比不同的两个对象：45° 各按自己的宽高换算', async () => {
    await seed([box('a', 40, 20), box('b', 20, 40)])
    await mount(<GradientTools objs={objs() as ShapeObject[]} target="fill" />)
    const input = host.querySelector<HTMLInputElement>('input[aria-label]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      input.focus()
      setter.call(input, '45')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
      input.blur()
    })
    for (const o of objs() as ShapeObject[]) {
      const g = o.fillGradient as Extract<Gradient, { kind: 'linear' }>
      expect(linearAngle(g, o.w, o.h)).toBeCloseTo(45, 0)
    }
  })
})
