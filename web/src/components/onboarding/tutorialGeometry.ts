import { useLayoutEffect, useState, type RefObject } from 'react'

/**
 * 教程里「图上某个元素此刻在哪」的量法（`tutorialLayout` 的环与提示、第一步的箭头都用它）。
 * **不写固定像素坐标**：每次按目标元素此刻的 `getBoundingClientRect` 现量，窗口缩放、字号变化、
 * 图例挪动之后跟着重量（ResizeObserver + 依赖变化），不会与目标错位。
 */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** 这次聚焦是不是键盘带来的（`:focus-visible`）；引擎不认这个选择器时按「是」处理——宁可多画一圈环 */
export function isFocusVisible(el: Element): boolean {
  try {
    return el.matches(':focus-visible')
  } catch {
    return true
  }
}

/** 等目标挂上最多等几帧（约半秒） */
const ATTACH_FRAMES = 30

const sameRect = (a: Rect | null, b: Rect | null) =>
  a === b ||
  (!!a && !!b && Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5 && Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5)

/**
 * `target` 在 `frame` 里的框（frame 自己的 CSS 像素；frame 被 transform 缩放时也换算对）。
 * 依赖变了、frame 尺寸变了、窗口缩放了都重量；量不到（还没挂上 / 隐藏）回 null。
 */
export function useTrackedRect(
  target: RefObject<Element | null>,
  frame: RefObject<HTMLElement | null>,
  deps: readonly unknown[],
): Rect | null {
  const [rect, setRect] = useState<Rect | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const t = target.current
      const f = frame.current
      if (!t || !f || !f.offsetWidth || !f.offsetHeight) {
        setRect((r) => (r === null ? r : null))
        return
      }
      const tb = t.getBoundingClientRect()
      const fb = f.getBoundingClientRect()
      const sx = fb.width / f.offsetWidth || 1
      const sy = fb.height / f.offsetHeight || 1
      const next = { x: (tb.left - fb.left) / sx, y: (tb.top - fb.top) / sy, w: tb.width / sx, h: tb.height / sy }
      setRect((r) => (sameRect(r, next) ? r : next))
    }
    // 目标与舞台可能晚一帧才挂上（舞台先量好自己的尺寸才渲染图）：等它们出现再开始量、再挂观察者，
    // 最多等 ATTACH_FRAMES 帧——等不到就是真的不在（这一步没画图），回 null
    let ro: ResizeObserver | null = null
    let raf = 0
    let tries = 0
    const attach = () => {
      if (!target.current || !frame.current) {
        if (tries++ < ATTACH_FRAMES) raf = requestAnimationFrame(attach)
        return
      }
      measure()
      if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(measure)
        ro.observe(frame.current)
      }
    }
    measure()
    attach()
    window.addEventListener('resize', measure)
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
      window.removeEventListener('resize', measure)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return rect
}
