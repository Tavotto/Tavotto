import { boundsOf } from '@/lib/geometry'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useViewportStore } from './viewportStore'

/**
 * 缩放到选区（⇧2 / 缩放菜单 / 命令面板，2026-10-07 设计审计 §10.1）：把选中对象的包围盒放进视野，
 * 与「适应画布」同一条补间（`fitRectAnimated`）。没有选中（或全是隐藏的）时什么都不做、返回 false——
 * 入口据此置灰或不说话，不去适应整页冒充。
 */
export function zoomToSelection(): boolean {
  const box = selectionBox()
  if (!box) return false
  useViewportStore.getState().fitRectAnimated(box)
  return true
}

/** 选区里可见对象的包围盒；没有就是 null（入口据此置灰） */
export function selectionBox(): { x: number; y: number; w: number; h: number } | null {
  const ids = useSelectionStore.getState().ids
  const objs = useDocumentStore.getState().doc.objects.filter((o) => ids.includes(o.id) && !o.hidden)
  const box = objs.length ? boundsOf(objs) : null
  return box && box.w > 0 && box.h > 0 ? box : null
}
