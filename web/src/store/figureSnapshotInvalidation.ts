import type { CapturedFigureDescriptor } from '@/lib/api'
import { capturedFigureSources } from '@/lib/capturedFigureSources'
import { currentProjectId } from '@/lib/session'
import { invalidateFigureSnapshots, useDocumentStore } from '@/store/documentStore'
import type { CanvasObject } from '@/types/document'

/** A successful explicit execution affects its sources on every canvas, including inactive ones. */
export function invalidateCapturedFigureSnapshots(
  descriptors: readonly CapturedFigureDescriptor[],
  pj: string | null,
): void {
  if (currentProjectId() !== pj) return
  const state = useDocumentStore.getState()
  const objects = state.buildProject().canvases.flatMap((c) => c.objects)
  // A removed panel may return through undo after an in-flight save responds. Its old source still
  // needs a generation bump, including alternative disk formats sharing the captured Figure stem.
  const seen = new WeakSet<object>()
  const appendHistoricalPanels = (value: unknown): void => {
    if (!value || typeof value !== 'object' || seen.has(value)) return
    seen.add(value)
    const object = value as Record<string, unknown>
    if (object.type === 'panel' && typeof object.fileId === 'string' && Array.isArray(object.overrides)) {
      objects.push(object as unknown as CanvasObject)
      return
    }
    for (const child of Object.values(object)) appendHistoricalPanels(child)
  }
  appendHistoricalPanels([state.past, state.future, state.canvasSessions, state.txn])
  invalidateFigureSnapshots(capturedFigureSources(descriptors, objects), pj)
}
