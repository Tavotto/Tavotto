/**
 * 图标微动效的播放端（ADR 0154）：动作的登记在 `icons/motion.ts`、关键帧在 `icons/motion.css`，
 * 这里只管「什么时候播」——点按钮播 `press`、指针进按钮播 `hover`、`playIcon` 手动重播。
 * 不放进 `icons/`：那里只出图标组件（`iconography.test` 的别名门禁）。
 */
import type { MotionTrigger } from './icons/motion'

const reducedMotion = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** 从头再播一次这枚图标的动作（没登记动作的图标、减少动态效果时什么都不做） */
export function playIcon(svg: Element) {
  if (!svg.hasAttribute('data-motion') || reducedMotion()) return
  svg.removeAttribute('data-play')
  // 读一次布局让「去掉 → 加回」成为两帧，关键帧才会从头开始
  void (svg as SVGElement).getBoundingClientRect()
  svg.setAttribute('data-play', '')
}

// 「能点的东西」：图标归属于离它最近的这一层
const INTERACTIVE = [
  'button',
  'a[href]',
  ...['button', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab', 'option', 'checkbox'].map((r) => `[role="${r}"]`),
].join(', ')

const isDisabled = (el: Element) => el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true'

/** 归属于 `host` 本身（而不是它里面另一颗按钮）的图标；给了 `on` 就只要按它触发的 */
function iconsOf(host: Element, on?: MotionTrigger) {
  const sel = on ? `svg[data-motion="${on}"]` : 'svg.icon'
  return Array.from(host.querySelectorAll(sel)).filter((svg) => svg.parentElement?.closest(INTERACTIVE) === host)
}

/**
 * 通用轻点：按钮里没有自己「点一下」动作的图标，点的时候整枚轻按一下再弹回（`motion.css` 的 `[data-tap]`），
 * 免得一颗按钮点了毫无反应。
 */
export function tapIcon(svg: Element) {
  if (reducedMotion()) return
  svg.removeAttribute('data-tap')
  void (svg as SVGElement).getBoundingClientRect()
  svg.setAttribute('data-tap', '')
}

let installed = false

/**
 * 全局只装一次（`main.tsx`）：点按钮播 `press`（没有点按动作的图标轻点一下）、指针进按钮播 `hover`。
 * 委托在 document 上而不是每枚图标各挂监听——图标集被三百多处用，99% 的时刻没人在点它们。
 */
export function installIconMotion(doc: Document = document) {
  if (installed) return
  installed = true
  doc.addEventListener('click', (e) => {
    const host = (e.target as Element | null)?.closest?.(INTERACTIVE)
    if (!host || isDisabled(host)) return
    // 等这次点击引起的重渲染落地：按钮里若换了图标，播的是换上的那枚
    requestAnimationFrame(() => {
      if (!host.isConnected) return
      for (const svg of iconsOf(host)) {
        if (svg.getAttribute('data-motion') === 'press') playIcon(svg)
        else tapIcon(svg)
      }
    })
  })
  // 列表里被高亮的那一项（命令面板、下拉的 ↑↓ 或指针移过去）：它的图标不分触发方式播一次——
  // 命令面板的行多半是键盘走过去的，点了就执行并关面板，悬停 / 点按两种触发都看不见
  new MutationObserver((records) => {
    for (const r of records) {
      const el = r.target as Element
      if (el.getAttribute('aria-selected') !== 'true' || r.oldValue === 'true') continue
      if (!el.matches('[role="option"], [role="menuitem"], [role="tab"]')) continue
      for (const svg of iconsOf(el)) if (svg.hasAttribute('data-motion')) playIcon(svg)
    }
  }).observe(doc.documentElement, { subtree: true, attributes: true, attributeFilter: ['aria-selected'], attributeOldValue: true })
  doc.addEventListener('pointerover', (e) => {
    if (e.pointerType === 'touch') return
    const host = (e.target as Element | null)?.closest?.(INTERACTIVE)
    if (!host || isDisabled(host)) return
    const from = e.relatedTarget as Node | null
    if (from && host.contains(from)) return
    iconsOf(host, 'hover').forEach(playIcon)
  })
}

/** 仅供用例：把「已安装」复位，让每个用例自己的 document 都能装上 */
export function _resetIconMotionForTest() {
  installed = false
}
