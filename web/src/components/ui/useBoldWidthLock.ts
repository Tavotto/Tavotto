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
