import type { ExportRequest } from '@/lib/api'
import { buildExportRequest, originalAvailability } from '@/lib/exportRequest'
import { frameCanvasOf, isZeroPad } from '@/lib/framePad'
import { annotationsBelow, annotationsOf, singleAnnotatedDocument } from '@/lib/singleExportDoc'
import { useDocumentStore } from '@/store/documentStore'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { PanelObject } from '@/types/document'

/**
 * 「分享审阅」要引擎渲染什么（ADR 0187 / local-api 补充）：就是导出那条路径里的 `original` 段（单图编辑）或 `canvas` 段（排版）
 * ——**原样取自 `buildExportRequest`**，不另拼。单图带标注 / 画布留白的取法与导出对话框同一份。
 * 取不出来（图的源文件不在等）返回 null，调用方按 `render_failed` 说。
 */
export function buildReviewRender(): Pick<ExportRequest, 'original' | 'canvas'> | null {
  const { doc, documentId } = useDocumentStore.getState()
  const ws = useWorkspaceStore.getState()
  const base = { formats: ['png'], filename: 'review', ppi: 300, documentId, doc } as const
  try {
    if (ws.mode === 'fast_edit' && ws.activePanelId) {
      const panel = doc.objects.find((o): o is PanelObject => o.id === ws.activePanelId && o.type === 'panel') ?? null
      if (!panel) return null
      const av = originalAvailability(panel.fileId, { panel })
      if (!av.ok) return null
      const manifest = exactPanelManifest(useRenderStore.getState(), panel)
      const framePad = !(isZeroPad(doc.framePad) && !doc.framePadTight?.length) ? frameCanvasOf(doc, panel, manifest).pad : null
      const annotated = singleAnnotatedDocument(doc, panel, av.spec, framePad ?? undefined)
      const { request } = buildExportRequest({
        ...base,
        scope: 'original',
        figureId: panel.fileId,
        panel,
        spec: av.spec,
        annotations: annotated ? annotationsOf(annotated) : undefined,
        annotationsBelow: annotated ? annotationsBelow(annotated) : 0,
        framePad,
      })
      return request.original ? { original: request.original } : null
    }
    const { request } = buildExportRequest({ ...base, scope: 'canvas' })
    return request.canvas ? { canvas: request.canvas } : null
  } catch {
    return null
  }
}
