import { useState, useSyncExternalStore } from 'react'

/**
 * 「主键按着的时候，界面上别冒出新控件」——按下与松开之间把一份值定格住。
 *
 * 原生 click 要求 pointerdown 与 pointerup 落在同一个元素上。首次渲染、校验结果这类
 * 异步到达的事实如果恰好在按下与松开之间让属性栏多出一颗按钮（身份头的「n 个问题」
 * 胶囊），被按着的那颗控件的邻居就变了——e2e `layout-timeline` 的「首次渲染在水平翻转
 * 按下与松开间完成」量的正是这件事（按钮 / 输入框的集合与位置不许变）。
 *
 * 只延后**显示**，不丢事实：松开（或取消、窗口失焦）的那一刻立刻换成最新值。
 * 传进来的值要是引用稳定的（store 里的原数组），否则每次渲染都算「变了」。
 */
let held = false
const listeners = new Set<() => void>()
const set = (v: boolean) => {
  if (held === v) return
  held = v
  for (const l of listeners) l()
}
const down = (e: PointerEvent) => {
  if (e.button === 0) set(true)
}
const up = () => set(false)

function subscribe(listener: () => void) {
  if (listeners.size === 0 && typeof window !== 'undefined') {
    window.addEventListener('pointerdown', down, true)
    window.addEventListener('pointerup', up, true)
    window.addEventListener('pointercancel', up, true)
    window.addEventListener('blur', up)
  }
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && typeof window !== 'undefined') {
      window.removeEventListener('pointerdown', down, true)
      window.removeEventListener('pointerup', up, true)
      window.removeEventListener('pointercancel', up, true)
      window.removeEventListener('blur', up)
      held = false
    }
  }
}

export function usePointerHeld(): boolean {
  return useSyncExternalStore(subscribe, () => held, () => false)
}

export function useHeldWhilePointerDown<T>(value: T): T {
  const isHeld = usePointerHeld()
  const [kept, setKept] = useState(value)
  // 没按着时跟上最新值（渲染期派生 state 的标准写法：值没变就不 set，不会循环）
  if (!isHeld && kept !== value) setKept(value)
  return isHeld ? kept : value
}
