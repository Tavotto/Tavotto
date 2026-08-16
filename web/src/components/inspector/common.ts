import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import type { CanvasObject } from '@/types/document'

/** 当前选中的对象（保持选择顺序，末位为主选） */
export function useSelectedObjects(): CanvasObject[] {
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  return ids
    .map((id) => objects.find((o) => o.id === id))
    .filter((o): o is CanvasObject => o != null)
}

/** 多选时若各对象取值不同则返回 undefined，用于显示「多个值」 */
export function shared<T>(objs: CanvasObject[], pick: (o: CanvasObject) => T): T | undefined {
  if (!objs.length) return undefined
  const first = pick(objs[0])
  return objs.every((o) => pick(o) === first) ? first : undefined
}
