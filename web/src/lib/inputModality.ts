/**
 * 输入方式：最近一次**导航性**输入是指针还是键盘（2026-10-31 UI 审计 D4）。
 *
 * 为什么要自己记：浏览器的 `:focus-visible` 启发式对「程序化 focus」不稳——Enter 进图内编辑后焦点被我们
 * 挪到左轨按钮、Radix 菜单关闭把焦点还给触发钮，WebKit（Tauri mac）会在**鼠标操作之后**照样画出键盘焦点环。
 * `focus({ focusVisible: false })` 在 WebKit 上不可用，且 47 处 `.focus()` 逐个改既漏又散，所以收成一个事实：
 * `<html data-input-modality="pointer">` 时 `index.css` 把 `:focus-visible` 的 outline 关掉；没有这个属性（启动后
 * 还没有任何指针操作）或值为 `keyboard` 时一切照旧。
 *
 * 切换判据（**只有导航键算键盘**）：Tab / 方向键 / Home / End / PageUp / PageDown → `keyboard`；
 * `pointerdown` / `mousedown` → `pointer`。Enter / Space / Esc 是「操作」不是「导航」——鼠标点中画布后按 Enter，
 * 方式仍是指针，程序化挪来的焦点不画环；纯键盘用户一定是用 Tab / 方向键到达控件的，环照常在。
 * 修饰键组合（⌘ / Ctrl / Alt）不切换：系统快捷键与导航无关。
 */
export type InputModality = 'pointer' | 'keyboard'

const ATTR = 'data-input-modality'

const NAV_KEYS = new Set(['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'])

let installed: (() => void) | null = null

export function inputModality(root: HTMLElement = document.documentElement): InputModality | null {
  const v = root.getAttribute(ATTR)
  return v === 'pointer' || v === 'keyboard' ? v : null
}

/** 挂全局监听（幂等），返回卸载函数；浏览器 / Tauri / 单测共用 */
export function installInputModality(doc: Document = document): () => void {
  if (installed) return installed
  const root = doc.documentElement
  const set = (m: InputModality) => {
    if (root.getAttribute(ATTR) !== m) root.setAttribute(ATTR, m)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (NAV_KEYS.has(e.key)) set('keyboard')
  }
  const onPointer = () => set('pointer')
  doc.addEventListener('keydown', onKey, true)
  doc.addEventListener('pointerdown', onPointer, true)
  doc.addEventListener('mousedown', onPointer, true)
  installed = () => {
    doc.removeEventListener('keydown', onKey, true)
    doc.removeEventListener('pointerdown', onPointer, true)
    doc.removeEventListener('mousedown', onPointer, true)
    root.removeAttribute(ATTR)
    installed = null
  }
  return installed
}
