import type { FigureDocument, ProjectDocument } from '@/types/document'

export type PersistedProjectDocument = ProjectDocument

/** 仅按正文内容选择 schema 版本，不判模型合法；在场的坏状态也不能被丢掉。 */
export function containsChartState(document: FigureDocument | PersistedProjectDocument): boolean {
  const canvases = 'canvases' in document ? document.canvases : [document]
  return canvases.some(canvas => canvas.objects.some(object =>
    Object.hasOwn(object, 'chart') || Object.hasOwn(object, 'chartSource')))
}
