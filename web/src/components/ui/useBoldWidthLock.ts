import { useCallback, useLayoutEffect, useRef, type RefObject } from 'react'

/**
 * 「选中态加粗不挪邻居」的量宽钩子：布局前把元素临时加到 600 量一次宽度，写成自己的
 * `min-width`，之后 400 ↔ 600 切换时宽度不变（2026-09-15 打磨批次 A 用户拍板 600；二审 A4
 * 担心的正是 SF Pro 的 600 比 400 宽 2–3%，en-US 切页签时邻居挪 1.5px）。
 *
 * 不复制子元素——页签里有图标 / 计数 / 运行点，渲染两遍会让 textContent 类的判据翻倍。
 * 用内联 `font-weight` 而不是类名：调用方可能是任何元素（右栏 `Tab` 的 button、画布页签的
 * 可拖拽 div），不依赖某个类的存在。jsdom 里量出来是 0，等于没设。
 */
export function useBoldWidthLock(ref: RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const prev = el.style.fontWeight
    el.style.minWidth = ''
    el.style.fontWeight = '600'
    const w = el.getBoundingClientRect().width
    el.style.fontWeight = prev
    if (w > 0) el.style.minWidth = `${Math.ceil(w)}px`
  })
}

/** (字体 + 文字) → 400 与 600 的排版宽度。同名反复量只量一次；字体加载完成后整表作废 */
const boldWidthCache = new Map<string, { w400: number; w600: number }>()
if (typeof document !== 'undefined') {
  document.fonts?.addEventListener?.('loadingdone', () => boldWidthCache.clear())
}

/**
 * 树 / 列表行标签的**截断位置**锁（2026-10 UI 审计 G3）：选中行是 600，同一段字比 400 宽 2–3%，
 * 被 `truncate` 切断的长名字在选中那一下多吃一两个字，随选择跳动。`useBoldWidthLock` 管不了这件事
 * （那是给不截断的页签留 `min-width`；给截断的标签加 `min-width` 只会把行撑出去）。
 *
 * 做法：选中且**会被截断**（600 的宽度超出标签盒）时，用等量的负字距吃掉 600 比 400 多出的宽度——
 * 字仍是 600，整段宽度与 400 时一样，截断点不动。不截断的行什么都不做（加粗本来就不该被压窄）。
 * 量宽用 Range（不受 `overflow:hidden` 剪裁影响）。
 *
 * 成本：只在 `[active, text]` 变化时跑；量值按（字体，文字）缓存，同名不重复量，缓存命中时不写样式、
 * 不强制布局（只读一次 clientWidth）。元素树全选几百行、拖动画布时树跟着重渲染，不会变成 N×2 次同步布局。
 * `text` 由调用方传入名字字符串（不在 effect 里读 textContent 当依赖）；返回 callback ref，挂在标签元素上。jsdom 里量出来是 0，等于没设。
 * 局限：标签盒在选中期间被拖窄（改抽屉宽）时不重量，下一次选中 / 改名才重算。
 */
export function useBoldTrackingLock<T extends HTMLElement>(active: boolean, text: string) {
  const elRef = useRef<T | null>(null)
  const latest = useRef({ active, text })
  latest.current = { active, text }

  const apply = useCallback((el: HTMLElement) => {
    const { active, text } = latest.current
    el.style.letterSpacing = ''
    const chars = [...text].length
    if (!active || !chars) return
    const cs = getComputedStyle(el)
    const key = `${cs.fontFamily}|${cs.fontSize}|${text}`
    let hit = boldWidthCache.get(key)
    if (!hit) {
      const range = document.createRange()
      // jsdom 的 Range 没有 getBoundingClientRect：等于没设
      if (typeof range.getBoundingClientRect !== 'function') return
      range.selectNodeContents(el)
      const prev = el.style.fontWeight
      // 量的时候关掉过渡：prefers-reduced-motion 下全局给所有属性一个 0.01ms 的过渡（index.css），
      // 改字重后同一帧里量到的还是起点值，400 与 600 量成一样宽，锁就悄悄失效了
      const prevTransition = el.style.transition
      el.style.transition = 'none'
      el.style.fontWeight = '400'
      const w400 = range.getBoundingClientRect().width
      el.style.fontWeight = '600'
      const w600 = range.getBoundingClientRect().width
      el.style.fontWeight = prev
      el.style.transition = prevTransition
      hit = { w400, w600 }
      if (boldWidthCache.size > 500) boldWidthCache.clear()
      boldWidthCache.set(key, hit)
    }
    const delta = hit.w600 - hit.w400
    // 不截断（600 的宽度放得下标签盒）：不需要锁
    if (delta > 0.05 && hit.w600 > el.clientWidth) el.style.letterSpacing = `${-delta / chars}px`
  }, [])

  // 标签元素被换掉（行内改名：输入框顶替标签、关掉后标签重新挂载）时，[active, text] 都没变，
  // effect 不会重跑，新元素上就没有字距——所以挂载本身也要量（callback ref，提交阶段调用）
  const labelRef = useCallback(
    (el: T | null) => {
      elRef.current = el
      if (el) apply(el)
    },
    [apply],
  )
  useLayoutEffect(() => {
    if (elRef.current) apply(elRef.current)
  }, [active, text, apply])
  return labelRef
}
