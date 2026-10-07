import { boundsOf, visualBounds, type Rect } from '@/lib/geometry'
import type { CanvasObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useViewportStore } from './viewportStore'
import { fastEditPanelOf, useWorkspaceStore } from './workspace'

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
  const objs = renderedSelection(objects, ids, fastEditPanelId)
  const box = objs.length ? boundsOf(objs.map(visualBounds)) : null
  return box && box.w > 0 && box.h > 0 ? box : null
}

/**
 * 选区里**此刻画在舞台上的**对象：没隐藏，快速编辑里只有正在编辑的那张图（`fastEditPanelId`，排版里 null）。
 * 判据只有这一处——`selectionBoxOf`（缩放到选中）与标尺的选区带（`canvas/Rulers`）都读它，选区里挂着的
 * 看不见的版面对象既不参与取景、也不画进标尺带（Codex #833）。
 */
export function renderedSelection(
  objects: readonly CanvasObject[],
  ids: readonly string[],
  fastEditPanelId: string | null,
): CanvasObject[] {
  return objects.filter(
    (o) => ids.includes(o.id) && !o.hidden && (fastEditPanelId === null || o.id === fastEditPanelId),
  )
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

/**
 * 「适应」的取景框：快速编辑对着正在编辑的那张图，画布排版对着页面。**舞台双击、⌘1 / 缩放菜单 /
 * 命令面板 `fit`、画布工具条与画布菜单的「适应画布」都只读这一份**——快速编辑里 ⌘1 曾经适应整页
 * （页面纸在这一屏根本不画），而双击舞台适应那张图，同一个「适应」两个落点（Codex #833）。
 *
 * 面板的包围盒原点不一定在 (0,0)，而视口的 `fit` 只吃宽高——所以这里把**右下角**当框（`x+w`），
 * 图才不会被裁在视野外。取的是包围盒不是图幅：用户在画布上缩放过的面板，快速编辑照样把它整张放进
 * 视野（图幅是它的输出规格，不是它此刻在屏幕上占多大）。面板可能被拖到过页面左上角外面（x/y 为负）：
 * 框至少要有这张图那么大，否则 fit 出来的比例装不下它。对象不在了（删除的那一拍）退回页面。
 */
export function stageFitFrame(
  objects: readonly { id: string; x: number; y: number; w: number; h: number }[],
  page: { w: number; h: number },
  fastEditPanelId: string | null,
): { w: number; h: number } {
  const o = fastEditPanelId === null ? undefined : objects.find((x) => x.id === fastEditPanelId)
  if (!o) return { w: page.w, h: page.h }
  return { w: Math.max(o.x + o.w, o.w), h: Math.max(o.y + o.h, o.h) }
}

/** 按此刻的文档与工作区模式「适应」（带补间）——所有「适应画布」入口的动作 */
export function fitStage(): void {
  const doc = useDocumentStore.getState().doc
  const frame = stageFitFrame(doc.objects, doc.page, fastEditPanelOf(useWorkspaceStore.getState()))
  useViewportStore.getState().fitAnimated(frame.w, frame.h)
}
