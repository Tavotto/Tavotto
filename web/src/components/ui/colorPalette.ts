import { createContext, useContext, useSyncExternalStore } from 'react'

/**
 * `ColorField` 的取色面板（2026-10-07 设计审计 §9.2 P1：「颜色字段只在 title 里有 hex」）。
 *
 * 取色面板是**宿主给的**：属性栏在根上挂 `ColorFieldContext`（`rich` + 本文档用过的颜色），
 * 里面的每个 `ColorField` 才长出可编辑的 hex 框与「文档颜色 / 最近 / 无 / 系统取色器」弹层；
 * 浮动栏那种塞不下 hex 的地方不挂，色块照旧直接开系统取色盘——同一个控件，不是第二套。
 */
export interface ColorFieldHost {
  /** 色块旁带可编辑 hex、点色块开取色面板 */
  rich: boolean
  /** 本文档里已在用的颜色（去重、小写 #rrggbb），面板里的第一组 */
  documentColors: readonly string[]
}

export const ColorFieldContext = createContext<ColorFieldHost | null>(null)

export const useColorFieldHost = () => useContext(ColorFieldContext)

/** 合法色号 → 小写 #rrggbb；三位简写展开；不合法回 null */
export function normalizeHex(raw: string): string | null {
  const s = raw.trim().replace(/^#/, '').toLowerCase()
  if (/^[0-9a-f]{6}$/.test(s)) return `#${s}`
  if (/^[0-9a-f]{3}$/.test(s)) return `#${s.split('').map((c) => c + c).join('')}`
  return null
}

/* ------------------------------ 最近用过的颜色 ------------------------------ */

const RECENT_KEY = 'tavotto.colorField.recent'
const RECENT_MAX = 8
let recent: readonly string[] = load()
const listeners = new Set<() => void>()

function load(): readonly string[] {
  // 每个观看者自己的便利：读不到（隐私窗口 / 被禁）就是空的，不影响任何功能
  try {
    const v = JSON.parse(globalThis.localStorage?.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!normalizeHex(x)) : []
  } catch {
    return []
  }
}

/** 记一笔「刚用过」：放到最前、去重、最多 8 个 */
export function pushRecentColor(color: string): void {
  const c = normalizeHex(color)
  if (!c) return
  recent = [c, ...recent.filter((x) => x !== c)].slice(0, RECENT_MAX)
  try {
    globalThis.localStorage?.setItem(RECENT_KEY, JSON.stringify(recent))
  } catch {
    /* 存不下就只活在这一次会话里 */
  }
  listeners.forEach((l) => l())
}

/** 用例之间清掉（`src/test/setup.ts` 不认识它，用例自己调） */
export function resetRecentColors(): void {
  recent = []
  listeners.forEach((l) => l())
}

const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}

export const useRecentColors = () => useSyncExternalStore(subscribe, () => recent)
