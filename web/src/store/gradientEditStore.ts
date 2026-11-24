import { create } from 'zustand'

/**
 * 渐变编辑的会话状态（ADR 0186 第二期）：属性栏的色带与画布上的渐变柄**共用**这一份——
 * 在哪一侧上编辑（填充 / 描边）、选中的是第几个色标（按位置排序后的下标）。两边谁改了，另一边当场跟着亮。
 * 不进文档、不进历史；换选中对象时由使用方按对象 id 重置。
 */
export interface GradientEditState {
  /** 这份状态属于哪个对象（换了对象就当它是缺省） */
  objectId: string | null
  target: 'fill' | 'stroke'
  stop: number
  set: (objectId: string, patch: Partial<Pick<GradientEditState, 'target' | 'stop'>>) => void
}

export const useGradientEditStore = create<GradientEditState>((set, get) => ({
  objectId: null,
  target: 'fill',
  stop: 0,
  set: (objectId, patch) => {
    const cur = get()
    const base = cur.objectId === objectId ? cur : { target: 'fill' as const, stop: 0 }
    set({ objectId, target: patch.target ?? base.target, stop: patch.stop ?? base.stop })
  },
}))

/** 某个对象此刻的编辑状态（不是它的就给缺省） */
export function gradientEditFor(objectId: string, s: GradientEditState = useGradientEditStore.getState()) {
  return s.objectId === objectId ? { target: s.target, stop: s.stop } : { target: 'fill' as const, stop: 0 }
}
