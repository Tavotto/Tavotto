import { create } from 'zustand'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import type { PathObject } from '@/types/document'

/**
 * 直接选择（锚点编辑，ADR 0185 M1）的界面态：正在编辑哪条路径、选中了哪些锚点（`lib/pathEdit.anchorKey`）。
 * 不是文档的一部分：不进历史、不落盘。
 *
 * **生效与否只由 `activePathEdit()` 判**：记着的路径必须还在、还是路径、没锁、没隐藏，而且选区正好是它自己——
 * 换了选区 / 撤销掉了这条路径，编辑态自然失效，不需要每个入口记得来清它。
 */
interface PathEditState {
  pathId: string | null
  anchors: string[]
  enter: (id: string) => void
  exit: () => void
  setAnchors: (keys: string[]) => void
}

export const usePathEditStore = create<PathEditState>((set) => ({
  pathId: null,
  anchors: [],
  enter: (id) => set({ pathId: id, anchors: [] }),
  exit: () => set({ pathId: null, anchors: [] }),
  setAnchors: (keys) => set({ anchors: [...new Set(keys)] }),
}))

/** 能不能进锚点编辑：路径、没锁、没隐藏 */
export const canEditAnchors = (o: { type: string; locked?: boolean; hidden?: boolean } | undefined): o is PathObject =>
  !!o && o.type === 'path' && !o.locked && !o.hidden

/** 此刻生效的锚点编辑（见上）；没有 = null */
export function activePathEdit(
  st: Pick<PathEditState, 'pathId' | 'anchors'> = usePathEditStore.getState(),
  objects = useDocumentStore.getState().doc.objects,
  selection = useSelectionStore.getState().ids,
): { path: PathObject; anchors: string[] } | null {
  if (!st.pathId || selection.length !== 1 || selection[0] !== st.pathId) return null
  const o = objects.find((x) => x.id === st.pathId)
  return canEditAnchors(o) ? { path: o, anchors: st.anchors } : null
}

/** 组件订阅版 */
export function useActivePathEdit(): { path: PathObject; anchors: string[] } | null {
  const pathId = usePathEditStore((s) => s.pathId)
  const anchors = usePathEditStore((s) => s.anchors)
  const objects = useDocumentStore((s) => s.doc.objects)
  const selection = useSelectionStore((s) => s.ids)
  return activePathEdit({ pathId, anchors }, objects, selection)
}
