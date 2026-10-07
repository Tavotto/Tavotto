import { boundsOf, visualBounds, type Rect } from '@/lib/geometry'
import type { CanvasObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useViewportStore } from './viewportStore'
import { useWorkspaceStore } from './workspace'

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
 * 选区里**此刻画在舞台上的**对象看得见的轴对齐包围盒；没有就是 null。按 `visualBounds` 取每个对象的盒再并：
 * 旋转了的标注 x/y/w/h 是未旋转的盒，直接并会把转出去的角裁在视野外（Codex #833）。
 * 隐藏对象的 id 会留在选区里，这里滤掉；面积为 0 也算没有。
 *
 * `fastEditPanelId`：快速编辑正在编辑的那张图（`workspace.activePanelId`），排版里是 null。快速编辑这一屏
 * 只画这一张（`CanvasLayers only=`），⌘A 之后选区里还有一整版看不见的对象——取景只认这一张，选区里没有它
 * 就是没有可取景的东西（入口置灰、⇧2 不动视口），不把唯一看得见的图挪走 / 缩小去框一片空白（Codex #833）。
 */
export function selectionBoxOf(
  objects: readonly CanvasObject[],
  ids: readonly string[],
  fastEditPanelId: string | null,
): Rect | null {
  const objs = objects.filter(
    (o) => ids.includes(o.id) && !o.hidden && (fastEditPanelId === null || o.id === fastEditPanelId),
  )
  const box = objs.length ? boundsOf(objs.map(visualBounds)) : null
  return box && box.w > 0 && box.h > 0 ? box : null
}

/**
 * 「缩放到选中」可不可用——缩放菜单置灰、命令面板出不出现、⇧2 做不做，都只问这一个判据，
 * 与动作本身算的是同一个盒（全选的东西都隐藏了、快速编辑里选区不含正在编辑的那张图，就不可用，
 * 不是静默空转或框一片看不见的版面；Codex #833）。
 */
export function canZoomToSelection(
  objects: readonly CanvasObject[],
  ids: readonly string[],
  fastEditPanelId: string | null,
): boolean {
  return selectionBoxOf(objects, ids, fastEditPanelId) !== null
}

/** 快速编辑正在编辑的面板 id；排版里 null（不变式 `mode === 'fast_edit'` ⟺ `activePanelId !== null`） */
const fastEditPanelOf = (s: { mode: string; activePanelId: string | null }) =>
  s.mode === 'fast_edit' ? s.activePanelId : null

/** 当前文档 + 当前选区 + 当前工作区模式的 `selectionBoxOf` */
export function selectionBox(): Rect | null {
  return selectionBoxOf(
    useDocumentStore.getState().doc.objects,
    useSelectionStore.getState().ids,
    fastEditPanelOf(useWorkspaceStore.getState()),
  )
}

/** 命令面板这类「问一次」的入口用：此刻可不可用 */
export function canZoomToSelectionNow(): boolean {
  return selectionBox() !== null
}

/** 缩放菜单这类要随状态重渲染的入口用：订阅文档对象、选区与工作区模式，判据同上 */
export function useCanZoomToSelection(): boolean {
  const objects = useDocumentStore((s) => s.doc.objects)
  const ids = useSelectionStore((s) => s.ids)
  const fastEditPanelId = useWorkspaceStore(fastEditPanelOf)
  return canZoomToSelection(objects, ids, fastEditPanelId)
}
