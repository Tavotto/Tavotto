import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'

/**
 * 「跟着选中项走」的指示物：页签的下划线、分段选择器的选中底（2026-09-14 二审 E2）。
 *
 * 此前两者都是每一格自己画一份（`after:` 伪元素 / 每格的 `bg-selected`），切换时旧的消失、
 * 新的出现，中间没有轨迹——用户每天做几百次的两种切换恰好是没被「解释」的变化。
 * 现在整组只渲染**一个**指示物，按当前选中项的 `offsetLeft / offsetWidth` 用
 * `transform` 滑过去（宪法第七节 2026-09-14 的修订：位置跟随型指示物允许在同一控件内滑动）。
 *
 * 三条约束：
 *   - 首次落位不播动画（挂载 / 从无选中到有选中时直接出现），之后才过渡；
 *   - 只动 transform 与 width，不触发邻居重排；`prefers-reduced-motion` 由 index.css 的
 *     全局兜底关掉过渡；
 *   - 量的是 DOM 几何：字重、语言切换、容器改宽（ResizeObserver）都会重新量。
 *
 * jsdom 里 offsetLeft / offsetWidth 恒为 0，指示物宽 0——用例要验位置就自己给这两个属性装值。
 */
export function useSlidingIndicator(
  root: RefObject<HTMLElement | null>,
  activeSelector: string,
): { style: CSSProperties | null; animate: boolean } {
  const [box, setBox] = useState<{ x: number; w: number } | null>(null)
  const [animate, setAnimate] = useState(false)
  const measure = useCallback(() => {
    const el = root.current
    if (!el) return
    const active = el.querySelector<HTMLElement>(activeSelector)
    if (!active) {
      setBox(null)
      return
    }
    const x = active.offsetLeft
    const w = active.offsetWidth
    setBox((prev) => (prev && prev.x === x && prev.w === w ? prev : { x, w }))
  }, [root, activeSelector])

  // 每次渲染都量一次：选中项、标签文字、字重都可能变，父组件重渲染就是信号
  useLayoutEffect(measure)

  useEffect(() => {
    const el = root.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [root, measure])

  // 第一次有位置时不过渡（直接落位），之后的移动才滑
  const seen = useRef(false)
  useEffect(() => {
    if (box && !seen.current) {
      seen.current = true
      setAnimate(true)
    }
    if (!box) {
      seen.current = false
      setAnimate(false)
    }
  }, [box])

  return {
    style: box ? { width: box.w, transform: `translateX(${box.x}px)` } : null,
    animate,
  }
}
