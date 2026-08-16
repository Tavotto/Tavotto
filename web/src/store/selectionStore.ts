import { create } from 'zustand'

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

export const useSelectionStore = create<SelectionState>((set, get) => ({
  ids: [],
  set: (ids) => {
    if (!same(get().ids, ids)) set({ ids })
  },
  add: (id) => set((s) => (s.ids.includes(id) ? s : { ids: [...s.ids, id] })),
  toggle: (id) =>
    set((s) => ({
      ids: s.ids.includes(id) ? s.ids.filter((v) => v !== id) : [...s.ids, id],
    })),
  clear: () => set((s) => (s.ids.length ? { ids: [] } : s)),
  primary: () => get().ids.at(-1) ?? null,
  has: (id) => get().ids.includes(id),
  prune: (alive) =>
    set((s) => {
      const next = s.ids.filter((id) => alive.has(id))
      return next.length === s.ids.length ? s : { ids: next }
    }),
}))
