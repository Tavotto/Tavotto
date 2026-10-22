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
export function useBoldWidthLock(ref: RefObject<HTMLElement | null>, enabled = true) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    const prev = el.style.fontWeight
    el.style.minWidth = ''
    el.style.fontWeight = '600'
    const w = el.getBoundingClientRect().width
    el.style.fontWeight = prev
    if (w > 0) el.style.minWidth = `${Math.ceil(w)}px`
  })
}

/** (字体 + 文字) → 400 与 600 的排版宽度。同名反复量只量一次；字体加载完成后整表作废 */
const boldWidthCache = new Map<string, { w400: number; w600eff: number; units: number }>()
if (typeof document !== 'undefined') {
  document.fonts?.addEventListener?.('loadingdone', () => boldWidthCache.clear())
}

/**
 * 选中行标签的尺寸观察：整个应用共用**一个** ResizeObserver（懒建），按元素登记回调。
 * 选中期间拖窄 / 拉宽抽屉会改变「放不放得下」，回调里只用缓存的 w400 / w600eff 对新的 clientWidth 重判，不重新量 Range。
 */
let sharedRO: ResizeObserver | null = null
const roCallbacks = new WeakMap<Element, () => void>()
function watchSize(el: HTMLElement, recheck: () => void) {
  if (typeof ResizeObserver === 'undefined') return
  sharedRO ??= new ResizeObserver((entries) => {
    for (const e of entries) roCallbacks.get(e.target)?.()
  })
  roCallbacks.set(el, recheck)
  sharedRO.observe(el)
}
function unwatchSize(el: HTMLElement) {
  if (!roCallbacks.has(el)) return
  roCallbacks.delete(el)
  sharedRO?.unobserve(el)
}

/**
 * 树 / 列表行标签的**截断位置**锁（2026-10 UI 审计 G3）：选中行是 600，同一段字比 400 宽 2–3%，
 * 被 `truncate` 切断的长名字在选中那一下多吃一两个字，随选择跳动。`useBoldWidthLock` 管不了这件事
 * （那是给不截断的页签留 `min-width`；给截断的标签加 `min-width` 只会把行撑出去）。
 *
 * 做法：选中且**会被截断**（600 的宽度超出标签盒）时，用等量的负字距吃掉 600 比 400 多出的宽度——
 * 字距 = 差值 ÷ 单位数，单位数是量出来的（600 下字距 1px 与 2px 的宽度差），不是数出来的——
 * 字仍是 600，整段宽度与 400 时一样，截断点不动。不截断的行什么都不做（加粗本来就不该被压窄）。
 * 量宽用 Range（不受 `overflow:hidden` 剪裁影响）。
 *
 * 成本：只在 `[active, text]` 变化时跑；量值按（字体，文字）缓存，同名不重复量，缓存命中时不写样式、
 * 不强制布局（只读一次 clientWidth）。元素树全选几百行、拖动画布时树跟着重渲染，不会变成 N×2 次同步布局。
 * `text` 由调用方传入名字字符串（不在 effect 里读 textContent 当依赖）；返回 callback ref，挂在标签元素上。jsdom 里量出来是 0，等于没设。
 * 标签盒尺寸变了（改抽屉宽）时由共用的 ResizeObserver 重判，只对选中行观察，取消选中 / 卸载即断开。
 */
export function useBoldTrackingLock<T extends HTMLElement>(active: boolean, text: string) {
  const elRef = useRef<T | null>(null)
  const latest = useRef({ active, text })
  latest.current = { active, text }

  const apply = useCallback((el: HTMLElement) => {
    const { active, text } = latest.current
    el.style.letterSpacing = ''
    // 每次都先撤掉上一次装的观察回调：它捕获的是旧文字的宽度，改名后留着，之后调抽屉宽会把旧字距套到新标签上
    unwatchSize(el)
    if (!active || !text) return
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
      // 选中后实际渲染时字距一定不是 0——浏览器在字距非 0 时关掉连字。所以 600 这一侧的基准也要在「连字关闭」
      // 的状态下量：分别量字距 1px 与 2px 的宽度，差值是**实际参与字距的单位数**（不数字符：`truncate` 折叠连续空白，
      // 组合符 / ZWJ 序列 / 连字都是一个单位，浏览器自己算），w600eff = w(1px) − units 是等效的零字距宽度。
      // 400 那一侧不处理：未选中时字距为 0、连字开着，原样量就是浏览器真正画出来的宽度
      el.style.fontWeight = '600'
      el.style.letterSpacing = '1px'
      const w1 = range.getBoundingClientRect().width
      el.style.letterSpacing = '2px'
      const w2 = range.getBoundingClientRect().width
      el.style.letterSpacing = ''
      el.style.fontWeight = prev
      el.style.transition = prevTransition
      const units = w2 - w1
      hit = { w400, w600eff: w1 - units, units }
      if (boldWidthCache.size > 500) boldWidthCache.clear()
      boldWidthCache.set(key, hit)
    }
    const { w400, w600eff, units } = hit
    const decide = () => {
      const delta = w600eff - w400
      // 不截断（600 的宽度放得下标签盒）：不需要锁
      el.style.letterSpacing =
        delta > 0.05 && units >= 0.5 && w600eff > el.clientWidth ? `${-delta / units}px` : ''
    }
    decide()
    if (w600eff - w400 > 0.05) watchSize(el, decide)
  }, [])

  // 标签元素被换掉（行内改名：输入框顶替标签、关掉后标签重新挂载）时，[active, text] 都没变，
  // effect 不会重跑，新元素上就没有字距——所以挂载本身也要量（callback ref，提交阶段调用）
  const labelRef = useCallback(
    (el: T | null) => {
      const prev = elRef.current
      if (prev && prev !== el) unwatchSize(prev)
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
