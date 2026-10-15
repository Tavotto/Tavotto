import { useLayoutEffect, type RefObject } from 'react'

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

/**
 * 树 / 列表行标签的**截断位置**锁（2026-10 UI 审计 G3）：选中行是 600，同一段字比 400 宽 2–3%，
 * 被 `truncate` 切断的长名字在选中那一下多吃一两个字，随选择跳动。`useBoldWidthLock` 管不了这件事
 * （那是给不截断的页签留 `min-width`；给截断的标签加 `min-width` 只会把行撑出去）。
 *
 * 做法：`active`（选中）时量同一段文字 400 与 600 的排版宽度之差，用等量的负字距吃掉——选中的字
 * 仍是 600，但整段宽度与 400 时一样，截断点不动、行尾的邻居也不动。字距只有零点几 px，看不出。
 * 量宽用 Range（不受 `overflow:hidden` 剪裁影响）。jsdom 里量出来是 0，等于没设。
 */
export function useBoldTrackingLock(ref: RefObject<HTMLElement | null>, active: boolean) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.letterSpacing = ''
    if (!active) return
    const text = el.textContent ?? ''
    const chars = [...text].length
    if (!chars) return
    const range = document.createRange()
    // jsdom 的 Range 没有 getBoundingClientRect：等于没设
    if (typeof range.getBoundingClientRect !== 'function') return
    range.selectNodeContents(el)
    const prev = el.style.fontWeight
    el.style.fontWeight = '400'
    const w400 = range.getBoundingClientRect().width
    el.style.fontWeight = '600'
    const w600 = range.getBoundingClientRect().width
    el.style.fontWeight = prev
    const delta = w600 - w400
    if (delta > 0.05) el.style.letterSpacing = `${-delta / chars}px`
  })
}
