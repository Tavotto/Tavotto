import { createContext } from 'react'

/**
 * 抽屉标题行的两个槽（`LeftPanel` 给、`DrawerHeader` 的 `DrawerHeaderActions` / `DrawerTitleMeta` 用）。
 * 单独一个文件：context 与组件同文件会让 fast refresh 整页重载。
 */
export interface DrawerSlots {
  meta: HTMLElement | null
  actions: HTMLElement | null
}

export const DrawerSlotsContext = createContext<DrawerSlots | null>(null)
