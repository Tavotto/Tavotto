/**
 * 卡片拖放契约（§29.5）：拖起有状态、进台高亮、离台取消、drop 只启动一次、
 * 取消不启动、拖过的 click 被吞掉、点击 fallback 正常。
 *
 * jsdom 没有 PointerEvent 也没有布局——指针事件用带 expando 的 MouseEvent
 * 模拟（React 从 nativeEvent 上读 pointerType），试验台矩形直接 mock。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FEATURED_EXAMPLE } from '../examples'
import { ExampleCard, type CardDragEvent } from './ExampleCard'

let container: HTMLDivElement
let root: Root
let stageEl: HTMLDivElement

//: 试验台占屏幕右半边（jsdom 量不出布局，矩形直接钉死）
const STAGE_RECT = { left: 500, top: 100, right: 900, bottom: 500 } as DOMRect

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  stageEl = document.createElement('div')
  vi.spyOn(stageEl, 'getBoundingClientRect').mockReturnValue({
    ...STAGE_RECT,
    width: 400,
    height: 400,
    x: 500,
    y: 100,
    toJSON: () => ({}),
  } as DOMRect)
  // jsdom 没有 pointer capture API
  HTMLElement.prototype.setPointerCapture ??= () => {}
  HTMLElement.prototype.releasePointerCapture ??= () => {}
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

const pointerEvent = (type: string, x: number, y: number, pointerType = 'mouse') => {
  const e = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })
  Object.defineProperty(e, 'pointerType', { value: pointerType })
  Object.defineProperty(e, 'pointerId', { value: 1 })
  return e
}

function renderCard(onLaunch = vi.fn(), onDragChange = vi.fn()) {
  act(() => {
    root.render(
      <ExampleCard
        example={FEATURED_EXAMPLE}
        stageRef={{ current: stageEl }}
        onLaunch={onLaunch}
        onViewCode={vi.fn()}
        onDragChange={onDragChange}
      />,
    )
  })
  const card = container.querySelector<HTMLElement>('[data-example-card]')!
  return { card, onLaunch, onDragChange }
}

const drags = (fn: ReturnType<typeof vi.fn>): (CardDragEvent | null)[] =>
  fn.mock.calls.map((c) => c[0])

describe('ExampleCard 拖放', () => {
  it('超过阈值才算拖起；拖起后卡片带 data-dragging，状态回报给 Landing', () => {
    const { card, onDragChange } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointermove', 102, 101)) // 阈值内：还不是拖
    })
    expect(card.hasAttribute('data-dragging')).toBe(false)
    act(() => {
      card.dispatchEvent(pointerEvent('pointermove', 140, 120))
    })
    expect(card.hasAttribute('data-dragging')).toBe(true)
    expect(drags(onDragChange).at(-1)).toMatchObject({ overStage: false })
  })

  it('拖进试验台 → overStage=true；拖出 → false', () => {
    const { card, onDragChange } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointermove', 600, 300)) // 台内
    })
    expect(drags(onDragChange).at(-1)).toMatchObject({ overStage: true })
    act(() => {
      card.dispatchEvent(pointerEvent('pointermove', 200, 300)) // 台外
    })
    expect(drags(onDragChange).at(-1)).toMatchObject({ overStage: false })
  })

  it('drop 在台上启动**一次**，且随后的 click 被吞掉', () => {
    const { card, onLaunch } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointermove', 600, 300))
      card.dispatchEvent(pointerEvent('pointerup', 600, 300))
      // 浏览器在 pointerup 后补发的那个 click
      card.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onLaunch).toHaveBeenCalledTimes(1)
  })

  it('drop 在台外不启动（但 click 也不误触发）', () => {
    const { card, onLaunch } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointermove', 300, 300))
      card.dispatchEvent(pointerEvent('pointerup', 300, 300))
      card.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onLaunch).not.toHaveBeenCalled()
  })

  it('pointercancel = 取消：不启动、拖动状态清空', () => {
    const { card, onLaunch, onDragChange } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointermove', 600, 300))
      card.dispatchEvent(pointerEvent('pointercancel', 600, 300))
    })
    expect(onLaunch).not.toHaveBeenCalled()
    expect(card.hasAttribute('data-dragging')).toBe(false)
    expect(drags(onDragChange).at(-1)).toBeNull()
  })

  it('没拖过的普通点击照常启动（点击 fallback）', () => {
    const { card, onLaunch } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100))
      card.dispatchEvent(pointerEvent('pointerup', 100, 100))
      card.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onLaunch).toHaveBeenCalledTimes(1)
  })

  it('触屏指针不进入拖动（滚动优先），点击仍然可用', () => {
    const { card, onLaunch } = renderCard()
    act(() => {
      card.dispatchEvent(pointerEvent('pointerdown', 100, 100, 'touch'))
      card.dispatchEvent(pointerEvent('pointermove', 600, 300, 'touch'))
      card.dispatchEvent(pointerEvent('pointerup', 600, 300, 'touch'))
    })
    expect(card.hasAttribute('data-dragging')).toBe(false)
    expect(onLaunch).not.toHaveBeenCalled()
    act(() => card.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(onLaunch).toHaveBeenCalledTimes(1)
  })
})
