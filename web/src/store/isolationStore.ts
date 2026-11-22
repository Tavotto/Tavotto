import { create } from 'zustand'
import { prefixOf } from '@/lib/groupTree'
import type { CanvasObject } from '@/types/document'
import { useDocumentStore } from './documentStore'

/**
 * 组内编辑（隔离模式，ADR 0191）：双击组里的对象进到那一层，组外的对象变淡、点不中；Esc 一层层退出。
 * 状态只有一条组链（外 → 内），不进文档、不进历史。链上的组没了（被拆 / 被删 / 换了画布）就退回还在的那一截。
 */
interface IsolationState {
  /** 正在编辑的组链，外 → 内；空 = 画布顶层 */
  path: string[]
  enter: (path: string[]) => void
  /** 退一层；返回退出的那个组 id（没有可退的 = null） */
  exitOne: () => string | null
  clear: () => void
}

export const useIsolationStore = create<IsolationState>((set, get) => ({
  path: [],
  enter: (path) => set({ path: [...path] }),
  exitOne: () => {
    const p = validPath(get().path, useDocumentStore.getState().doc.objects)
    if (!p.length) {
      if (get().path.length) set({ path: [] })
      return null
    }
    set({ path: p.slice(0, -1) })
    return p[p.length - 1]
  },
  clear: () => {
    if (get().path.length) set({ path: [] })
  },
}))

/** 链里还成立的最长前缀：每一层的组都还有对象，而且挂在同一条链上 */
export function validPath(path: readonly string[], objects: readonly CanvasObject[]): string[] {
  for (let n = path.length; n > 0; n--) {
    const pre = prefixOf(objects, path[n - 1])
    if (pre && pre.length === n && pre.every((g, i) => g === path[i])) return path.slice(0, n)
  }
  return []
}

/** 当前作用域（正在编辑的最内层组），顶层 = null；链失效的部分不算 */
export function isolationScope(objects: readonly CanvasObject[] = useDocumentStore.getState().doc.objects): string | null {
  return validPath(useIsolationStore.getState().path, objects).at(-1) ?? null
}

// 换画布 = 离开组内编辑（组 id 只在一张画布里有意义）
useDocumentStore.subscribe((s, prev) => {
  if (s.activeCanvasId !== prev.activeCanvasId) useIsolationStore.getState().clear()
})
