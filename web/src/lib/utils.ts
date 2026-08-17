import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const isMac =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

export const MOD = isMac ? '⌘' : 'Ctrl'
export const ALT = isMac ? '⌥' : 'Alt'

/**
 * 行文里的组合键：Mac 连写（`⌘Z`），其余平台用 + 连接（`Ctrl+Z`）——
 * 「CtrlZ 可撤销」在中文句子里读不通。快捷键表/菜单里的等宽键位标签
 * 仍直接拼 `${MOD}Z`，那儿是表格对齐，不是句子。
 * 提示文案里**不要再手写 ⌘/⌥**：Windows 用户的键盘上没有这两个键。
 *
 * 起名叫 combo 而不是 altKey()，是为了不和事件对象的 `ev.altKey` 撞
 * ——那个名字一 grep 就混。
 */
export function combo(mod: string, key: string): string {
  return isMac ? `${mod}${key}` : `${mod}+${key}`
}

/** `combo(MOD, key)` 的简写，绝大多数提示用的都是它 */
export function modKey(key: string): string {
  return combo(MOD, key)
}

/** 时间戳 → HH:MM，用于「已自动保存在本机」「最近文档」这类时点提示 */
export function formatClock(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
