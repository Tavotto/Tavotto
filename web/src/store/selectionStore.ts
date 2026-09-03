import { create } from 'zustand'
import { emitActivity } from '@/lib/activity'

interface SelectionState {
  /** 末位为主选对象（对齐 / 等宽等高的基准） */
  ids: string[]
  set: (ids: string[]) => void
  add: (id: string) => void
  toggle: (id: string) => void
  clear: () => void
  primary: () => string | null
  has: (id: string) => boolean
  /** 删除对象后清理悬空选择 */
  prune: (alive: ReadonlySet<string>) => void
}

const same = (a: string[], b: string[]) =>
  a.length === b.length && a.every((v, i) => v === b[i])

/**
 * 选区真的变了才发一声本地活动信号（只带数量，不带 id）。
 * `prune` 不发：那是删对象的副作用，不是用户在选东西。
 */
const announce = (before: string[], after: string[]) => {
  if (!same(before, after)) emitActivity({ kind: 'selection.changed', count: after.length })
}

export const useSelectionStore = create<SelectionState>((set, get) => ({
  ids: [],
  set: (ids) => {
    const before = get().ids
    if (!same(before, ids)) set({ ids })
    announce(before, get().ids)
  },
  add: (id) => {
    const before = get().ids
    set((s) => (s.ids.includes(id) ? s : { ids: [...s.ids, id] }))
    announce(before, get().ids)
  },
  toggle: (id) => {
    const before = get().ids
    set((s) => ({
      ids: s.ids.includes(id) ? s.ids.filter((v) => v !== id) : [...s.ids, id],
    }))
    announce(before, get().ids)
  },
  clear: () => {
    const before = get().ids
    set((s) => (s.ids.length ? { ids: [] } : s))
    announce(before, get().ids)
  },
  primary: () => get().ids.at(-1) ?? null,
  has: (id) => get().ids.includes(id),
  prune: (alive) =>
    set((s) => {
      const next = s.ids.filter((id) => alive.has(id))
      return next.length === s.ids.length ? s : { ids: next }
    }),
}))
