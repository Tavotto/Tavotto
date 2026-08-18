/**
 * 动效的公共地基。
 *
 * 三条纪律，改这里之前先读：
 *
 * 1. **时长与缓动只有一个出处。** CSS 侧在 `index.css` 的 `@theme`，JS 侧是
 *    下面的 `DURATION`；两边逐值相等由 `motion.test.ts` 直接读 index.css 断言。
 *    组件里**不写字面量毫秒数**——散开之后没人能再统一调它。
 * 2. **`prefers-reduced-motion` 是硬约束，不是建议。** index.css 的全局
 *    override 只管得到 CSS 的 animation/transition；**JS 动画一律管不到**。
 *    所以任何 rAF 动画都必须走 `tween()`——它在 reduced 时直接落终态，
 *    一帧都不放。绕过它自己写 rAF = 悄悄把这条无障碍契约作废。
 * 3. **动画只是点缀，关掉不损失任何信息。** 位移 ≤4px、scale 0.97~1、
 *    没有弹跳回弹；退场比进场短一档（退场是让路，拖沓会挡住下一步动作）。
 */
import { useEffect, useRef, useState } from 'react'

/** 与 index.css `@theme` 的 --duration-* 逐值同源（motion.test.ts 看护） */
export const DURATION = {
  fast: 120,
  base: 180,
  slow: 240,
  exit: 90,
} as const

export type DurationName = keyof typeof DURATION

/** 用户要求减少动效？CSS 有全局兜底，**JS 动画必须自己判**。 */
export function prefersReducedMotion(): boolean {
  return (
    typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/** 与 index.css 的 --ease-pop 同形（起步快、收尾稳） */
export const easeOutCubic = (t: number): number => 1 - (1 - t) ** 3

interface TweenOptions {
  duration?: number
  ease?: (t: number) => number
  /** 每帧回调，参数是**缓动后**的进度 0→1；结束那一帧保证正好是 1 */
  onUpdate: (progress: number) => void
  onDone?: () => void
}

/**
 * rAF 补间。返回 cancel。
 *
 * reduced-motion 下**同步**调用 `onUpdate(1)` + `onDone()` 后返回 —— 调用方
 * 拿到的是「已经到终点」，不需要再写一遍分支。这也让 e2e / 单测不必等动画。
 */
export function tween({
  duration = DURATION.base,
  ease = easeOutCubic,
  onUpdate,
  onDone,
}: TweenOptions): () => void {
  if (prefersReducedMotion() || duration <= 0) {
    onUpdate(1)
    onDone?.()
    return () => {}
  }

  let raf = 0
  let cancelled = false
  const t0 = performance.now()

  const step = (now: number) => {
    if (cancelled) return
    const t = Math.min(1, (now - t0) / duration)
    onUpdate(ease(t))
    if (t < 1) raf = requestAnimationFrame(step)
    else onDone?.()
  }
  raf = requestAnimationFrame(step)

  return () => {
    cancelled = true
    cancelAnimationFrame(raf)
  }
}

/**
 * 退场动画的保活：`open` 转 false 后再多挂 `exitMs`，让 CSS 的
 * `data-[state=closed]:animate-*` 有机会播完。
 *
 * Radix 的浮层自带 Presence（会等 animationend），**不要**给它们套这个；
 * 这是给自己写的条件渲染用的（toast、角标这类 `{x && <div/>}`）。
 *
 * reduced-motion 下 exitMs 视为 0：立刻卸载，不留一个空档期。
 */
export function usePresence(
  open: boolean,
  exitMs: number = DURATION.exit,
): { mounted: boolean; state: 'open' | 'closed' } {
  const [mounted, setMounted] = useState(open)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    clearTimeout(timer.current)
    if (open) {
      setMounted(true)
      return
    }
    // 已经卸载了就别再排一次定时器（否则每次 open=false 的重渲染都排一个）
    if (prefersReducedMotion() || exitMs <= 0) {
      setMounted(false)
      return
    }
    timer.current = setTimeout(() => setMounted(false), exitMs)
    return () => clearTimeout(timer.current)
  }, [open, exitMs])

  // 卸载后 state 取什么都无所谓（不会被渲染），保持 'closed' 语义清楚
  return { mounted, state: open ? 'open' : 'closed' }
}
