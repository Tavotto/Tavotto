/**
 * 问题面板的「现场」：当前图是谁、此刻的现场键（`problemList.problemContextKey`）。
 *
 * 面板的 hook（`useScopedProblems`）与点击处理器 / `openProblemAt` 共用这一份判据。
 * 后两者**在定位之后**现取（`problemContextNow`）——定位可能进了另一张图的快速编辑，
 * 用点击前那一帧的现场去盖章，卡片与游标下一帧就对不上号、被当成过期。
 */
import type { CanvasObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { effectiveScope, problemContextKey } from './problemList'

/**
 * 「当前图」= 快速编辑中的那张 → 图内编辑中的那张 → 选中的面板。
 * 三档合成一个 id：快速编辑的 `activePanelId` 与图内编辑的 `elementPanelId`
 * 正常情况下指同一个对象，第三档才是「排版模式下点了一张图」。
 */
export function currentFigureOf(
  candidates: readonly (string | null)[],
  objects: readonly CanvasObject[],
): { id: string | null; name: string | null } {
  for (const id of candidates) {
    if (!id) continue
    const o = objects.find((x) => x.id === id)
    if (o?.type === 'panel') return { id: o.id, name: o.name ?? o.fileId }
  }
  return { id: null, name: null }
}

/** 此刻的现场键（不是 hook）：点卡片、落游标时带上它 */
export function problemContextNow(): string {
  const ui = useUiStore.getState()
  const doc = useDocumentStore.getState()
  const figure = currentFigureOf(
    [useWorkspaceStore.getState().activePanelId, ui.elementPanelId, useSelectionStore.getState().ids.at(-1) ?? null],
    doc.doc.objects,
  )
  return problemContextKey({
    loadSeq: doc.loadSeq,
    scope: effectiveScope(ui.problemScope, figure.id),
    figureId: figure.id,
    view: ui.problemView,
  })
}
