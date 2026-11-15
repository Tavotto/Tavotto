import type { ExportRequest } from '@/lib/api'
import { buildExportRequest, originalAvailability } from '@/lib/exportRequest'
import { isKnownObject } from '@/types/document'
import { frameCanvasOf, isZeroPad } from '@/lib/framePad'
import { annotationsBelow, annotationsOf, singleAnnotatedDocument } from '@/lib/singleExportDoc'
import { useDocumentStore } from '@/store/documentStore'
import { exactPanelManifest, useRenderStore } from '@/store/renderStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { PanelObject } from '@/types/document'

/** 此刻 render 输入的签名（djb2 over JSON）：页面尺寸 / 留白 / 对象 / 图内修改任何一项变了它就变；取不出 render = 固定串 */
export function reviewRenderSignature(): string {
  const json = JSON.stringify(buildReviewRender()) ?? 'null'
  let h = 5381
  for (let i = 0; i < json.length; i++) h = ((h << 5) + h + json.charCodeAt(i)) | 0
  return `${json.length}:${h}`
}

const OBJECT_ID = /^[A-Za-z0-9_-]{1,64}$/

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
    if (!request.canvas) return null
    // 排版里每个面板带文档对象自己的不透明 id：导出请求里的 `id` 是图的项目内路径，不能出机（引擎只认 object_id）。
    // 与 `toExportObjects` 同一条过滤（隐藏的、不认识的不进载荷），所以按下标对应；面板名不含路径分隔符时一并带作显示名
    const visible = doc.objects.filter((o) => !o.hidden && isKnownObject(o))
    const objects = request.canvas.objects.map((e, i) => {
      const o = visible[i]
      if (!o || o.type !== 'panel' || !OBJECT_ID.test(o.id)) return e
      const label = o.name && !/[\\/]/.test(o.name) ? { label: o.name } : {}
      return { ...e, object_id: o.id, ...label }
    })
    return { canvas: { ...request.canvas, objects } }
  } catch {
    return null
  }
}
