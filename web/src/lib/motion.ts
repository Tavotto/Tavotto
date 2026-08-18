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
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from 'react'

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

/**
 * 进场缓动的 CSS 写法，给 Web Animations API 用（它只认字符串）。
 * 与 index.css 的 `--ease-pop` 逐字节相同，motion.test 直接读那份文件比对。
 */
export const EASE_POP = 'cubic-bezier(0.16, 1, 0.3, 1)'

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

export type PresenceState = 'open' | 'closed'

interface DrawerMotionOptions {
  state: PresenceState
  /** 覆盖态（窄屏）：抽屉绝对定位盖在画布上，不占布局 */
  overlay: boolean
  width: number
  side: 'left' | 'right'
}

/**
 * 侧边抽屉的动效属性——**左右两个抽屉的唯一出处**，展开成 props 直接铺在
 * `<aside>` 上。
 *
 * 两种形态分得很清楚：
 *   停靠态动 `width`（画布必须跟着让位，这是布局变化，躲不掉）；
 *   覆盖态动 `transform`（不占布局，零重排）。
 *
 * 用的时候有一条硬要求：**抽屉内容必须包在一层定宽的内层里，外层
 * `overflow: hidden`**。否则动 width 的那 180ms 里子树每帧重排，文字会跟着
 * 挤来挤去；包起来之后每帧重排的只剩画布列，抽屉自己的子树一次都不动。
 */
export function drawerMotion({ state, overlay, width, side }: DrawerMotionOptions): {
  'data-state': PresenceState
  className: string
  style: CSSProperties
} {
  return {
    'data-state': state,
    className: overlay
      ? 'data-[state=open]:animate-drawer-slide-in data-[state=closed]:animate-drawer-slide-out'
      : 'data-[state=open]:animate-drawer-in data-[state=closed]:animate-drawer-out',
    style: {
      width,
      // 关键帧要的两个量：停靠态的目标宽度、覆盖态滑进滑出的方向
      '--drawer-w': `${width}px`,
      '--drawer-from': side === 'left' ? '-100%' : '100%',
    } as CSSProperties,
  }
}

/**
 * FLIP：列表顺序变了之后，让每一项从**原来的位置**滑到新位置。
 *
 * 用法：容器给 ref，每个可重排的子项带一个**稳定 id 属性**（默认
 * `data-flip-id`；已经有 `data-layer` 这类身份属性的列表直接把属性名传进来，
 * 不必在 DOM 上写第二份 id）。顺序没变就什么都不做，对普通重渲染无感。
 *
 * 两件事值得先知道再用：
 *
 * 1. **每次提交都要量一遍位置**（没有依赖数组）。FLIP 需要「上一帧的位置」，
 *    而 React 不给函数组件「DOM 改之前」的钩子，只能每次提交后量下来留着
 *    下次用。代价是每次重渲染一轮 getBoundingClientRect（一次强制布局）——
 *    列表长到几百行时值得换成别的方案，几十行的量级实测在噪声里。
 *    不能只在顺序变化时量：滚动、窗口缩放也会让位置变，拿着过期的基准去
 *    算位移，会凭空播出一段错误的滑动。
 * 2. **动画用 WAAPI 而不是 CSS transition**：不需要「先设起点、下一帧再设终点」
 *    那套两帧把戏，也不会和元素自己的 transform 类抢同一个属性声明。
 *    reduced-motion 下整个跳过——WAAPI 同样不受 index.css 的全局 override 管。
 */
export function useFlip(container: RefObject<HTMLElement | null>, attr = 'data-flip-id'): void {
  const prev = useRef(new Map<string, { left: number; top: number }>())

  useLayoutEffect(() => {
    const root = container.current
    if (!root) return
    const items = [...root.querySelectorAll<HTMLElement>(`[${attr}]`)]
    const next = new Map<string, { left: number; top: number }>()
    const reduced = prefersReducedMotion()
    // 位置一律换算到**容器的内容坐标系**（减容器左上角、加回滚动量），不能直接
    // 用视口坐标：列表能滚，而滚动不触发重渲染——下一次因为别的原因重渲染时，
    // 拿视口坐标算出来的位移正好等于这期间滚过的距离，整列表会凭空滑一下。
    const base = root.getBoundingClientRect()
    const ox = base.left - root.scrollLeft
    const oy = base.top - root.scrollTop

    for (const el of items) {
      const id = el.getAttribute(attr)
      if (!id) continue
      const r = el.getBoundingClientRect()
      next.set(id, { left: r.left - ox, top: r.top - oy })
      if (reduced) continue
      const old = prev.current.get(id)
      if (!old) continue // 新来的项没有「原来的位置」，直接就位
      const dx = old.left - (r.left - ox)
      const dy = old.top - (r.top - oy)
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue
      // jsdom 没有 Web Animations API（真实浏览器全都有）。不判这一下，
      // 任何渲染到这类列表的单测都会在这里 TypeError
      if (typeof el.animate !== 'function') continue
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }],
        { duration: DURATION.base, easing: EASE_POP },
      )
    }
    prev.current = next
  })
}

/**
 * 一次性的 FLIP：先记下这些元素现在在哪，DOM 改完之后让它们从原处滑到新处。
 *
 * 与 `useFlip` 的分工：`useFlip` 盯的是「这个列表每次重渲染都可能重排」；
 * 这个是「我**知道**接下来这一步会挪动它们」。画布对象必须走这条——对象位置
 * 每一帧都在因为拖动而变，挂个自动 FLIP 上去等于给每个拖动帧都播一次动画。
 *
 * 动的是 CSS 的 **`translate` 属性**而不是 `transform`：画布对象的 `transform`
 * 上挂着自己的 rotate/scale（旋转、翻转），拿 transform 播动画会把它们在这
 * 180ms 里整个盖掉——旋转过的面板会先转正、再转回去。
 *
 * 用法：
 *   const play = flipCapture(els)
 *   commit(...)        // 改文档
 *   play()             // 内部等 React 把 DOM 落下去再量第二次
 */
export function flipCapture(els: (HTMLElement | null | undefined)[]): () => void {
  if (prefersReducedMotion()) return () => {}
  const before = new Map<HTMLElement, DOMRect>()
  for (const el of els) if (el) before.set(el, el.getBoundingClientRect())
  if (!before.size) return () => {}

  return () => {
    // rAF 而不是微任务：要等 React 把这次提交渲染进 DOM
    requestAnimationFrame(() => {
      for (const [el, old] of before) {
        if (!el.isConnected || typeof el.animate !== 'function') continue
        const now = el.getBoundingClientRect()
        const dx = old.left - now.left
        const dy = old.top - now.top
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue
        el.animate([{ translate: `${dx}px ${dy}px` }, { translate: 'none' }], {
          duration: DURATION.base,
          easing: EASE_POP,
        })
      }
    })
  }
}
