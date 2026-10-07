import { boundsOf, visualBounds, type Rect } from '@/lib/geometry'
import type { CanvasObject } from '@/types/document'
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

/**
 * 选区里可见对象**看得见的**轴对齐包围盒；没有就是 null。按 `visualBounds` 取每个对象的盒再并：
 * 旋转了的标注 x/y/w/h 是未旋转的盒，直接并会把转出去的角裁在视野外（Codex #833）。
 * 隐藏对象的 id 会留在选区里，这里滤掉；面积为 0 也算没有。
 */
export function selectionBoxOf(objects: readonly CanvasObject[], ids: readonly string[]): Rect | null {
  const objs = objects.filter((o) => ids.includes(o.id) && !o.hidden)
  const box = objs.length ? boundsOf(objs.map(visualBounds)) : null
  return box && box.w > 0 && box.h > 0 ? box : null
}

/**
 * 「缩放到选中」可不可用——缩放菜单置灰、命令面板出不出现、⇧2 做不做，都只问这一个判据，
 * 与动作本身算的是同一个盒（全选的东西都隐藏了就不可用，不是静默空转；Codex #833）。
 */
export function canZoomToSelection(objects: readonly CanvasObject[], ids: readonly string[]): boolean {
  return selectionBoxOf(objects, ids) !== null
}

/** 当前文档 + 当前选区的 `selectionBoxOf` */
export function selectionBox(): Rect | null {
  return selectionBoxOf(useDocumentStore.getState().doc.objects, useSelectionStore.getState().ids)
}
