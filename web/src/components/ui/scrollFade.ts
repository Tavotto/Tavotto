import { useCallback, useEffect, useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react'

/**
 * 滚动溢出边缘的渐隐（2026-10-07 设计审计 §3.7 / §10.1 起在 CanvasTabs，抽成共享件）：条外还有内容的那一侧
 * `size` px 的 alpha mask，不叠色块（底下是什么颜色都对）。只在真的还能往那边滚时画，滚到头就收。
 *
 * 技术 = JS 量边界 + 内联 `mask-image`，不用 `animation-timeline`（macOS 14/15 的 WKWebView 不保证支持）。
 * mask 会连背景一起淡：挂在**内层滚动元素**上，别挂在带底色 / 圆角的外壳上。
 */
export type ScrollAxis = 'x' | 'y'
export interface ScrollFade {
  /** 起点侧（左 / 上）还能往回滚 */
  start: boolean
  /** 终点侧（右 / 下）还有内容 */
  end: boolean
}

const NONE: ScrollFade = { start: false, end: false }

/** 量一个滚动元素两侧是否还有内容（1px 容差吸收小数像素） */
export function measureScrollFade(el: HTMLElement, axis: ScrollAxis): ScrollFade {
  const pos = axis === 'x' ? el.scrollLeft : el.scrollTop
  const view = axis === 'x' ? el.clientWidth : el.clientHeight
  const total = axis === 'x' ? el.scrollWidth : el.scrollHeight
  return { start: pos > 1, end: pos + view < total - 1 }
}

/** 渐隐 mask 的内联样式；两侧都不需要时返回 undefined（元素上不留任何 mask） */
export function scrollFadeStyle(fade: ScrollFade, axis: ScrollAxis, size = 16): CSSProperties | undefined {
  if (!fade.start && !fade.end) return undefined
  const a = fade.start ? `transparent 0, #000 ${size}px` : '#000 0'
  const b = fade.end ? `#000 calc(100% - ${size}px), transparent 100%` : '#000 100%'
  const mask = `linear-gradient(${axis === 'x' ? 'to right' : 'to bottom'}, ${a}, ${b})`
  return { maskImage: mask, WebkitMaskImage: mask }
}

/**
 * 跟踪滚动元素两侧的溢出：被动 `scroll` 监听 + ResizeObserver（元素自身尺寸变），卸载时都摘掉；
 * 每次提交后也量一遍（内容增减不会触发 ResizeObserver）。返回的 `fade` 与 `scrollFadeStyle` 配用；
 * 需要在自己的布局副作用之后补量的调用方（比如先滚进视野再量）用 `measure`。
 */
export function useScrollFade(
  ref: RefObject<HTMLElement | null>,
  axis: ScrollAxis,
): { fade: ScrollFade; measure: () => void } {
  const [fade, setFade] = useState<ScrollFade>(NONE)
  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    const next = measureScrollFade(el, axis)
    setFade((f) => (f.start === next.start && f.end === next.end ? f : next))
  }, [ref, axis])

  useLayoutEffect(() => {
    measure()
  })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.addEventListener('scroll', measure, { passive: true })
    let ro: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(measure)
      ro.observe(el)
    }
    return () => {
      el.removeEventListener('scroll', measure)
      ro?.disconnect()
    }
  }, [ref, measure])

  return { fade, measure }
}
