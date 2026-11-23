import { PRODUCT_NAME } from './brand'
import { canvasToDoc, docToCanvas, SCHEMA_CURRENT, SCHEMA_CHART_PROJECT as CHART_DOCUMENT_SCHEMA } from '@/types/document'
import type { CanvasData, FigureDocument, ProjectDocument } from '@/types/document'

/** engine/documentformat.py 的格式闭集；缺省始终是 legacy。 */
export type DocumentFormat = 'legacy' | 'v4'
export interface DocumentAddress { format: DocumentFormat; id: string }

/** engine/documents.py 的显式计算项目格式；不改变旧 SCHEMA_CURRENT。 */
export { CHART_DOCUMENT_SCHEMA }
export type PersistedProjectDocument = ProjectDocument
export type ChartProjectDocument = PersistedProjectDocument & { schema: typeof CHART_DOCUMENT_SCHEMA }

export function parseDocumentFormat(value: unknown): DocumentFormat | null {
  return value === undefined || value === 'legacy' ? 'legacy' : value === 'v4' ? 'v4' : null
}

export function documentFormatForSchema(schema: unknown): DocumentFormat | null {
  return schema === 2 || schema === SCHEMA_CURRENT ? 'legacy' : schema === CHART_DOCUMENT_SCHEMA ? 'v4' : null
}

/** 仅选择持久化格式，不判模型合法；在场的坏状态也不能靠 legacy 丢弃。 */
export function containsChartState(document: FigureDocument | PersistedProjectDocument): boolean {
  const canvases = 'canvases' in document ? document.canvases : [document]
  return canvases.some(canvas => canvas.objects.some(object =>
    Object.hasOwn(object, 'chart') || Object.hasOwn(object, 'chartSource')))
}

/** 仅已实现的文档 API 进入 namespace；包导入/安装不借导出路由造别名。 */
export function documentApiPath(path: string, format: DocumentFormat = 'legacy'): string {
  if (format === 'legacy') return path
  if (path !== '/api/package' && !/^\/api\/(layouts|autosave|layout-session|versions)(?:[/?]|$)/.test(path)) {
    throw new Error('unsupported_document_route')
  }
  return `/api/documents/v4${path.slice(4)}`
}

/** 无格式参数的本机键逐字保留，v4 不读取或覆盖 legacy 键。 */
export function documentNamespaceKey(key: string, format: DocumentFormat = 'legacy'): string {
  if (format === 'legacy') return key
  const prefix = `${PRODUCT_NAME.toLowerCase()}.`
  if (!key.startsWith(prefix)) throw new Error('unsupported_document_key')
  return `${prefix}documents.v4.${key.slice(prefix.length)}`
}

/** 后端 working docId 为 [\w-]；v4 的 JSON tuple 与该旧 id 域分离。 */
export function documentAddressKey(id: string, format: DocumentFormat = 'legacy'): string {
  return format === 'legacy' ? id : JSON.stringify([format, id])
}

/** 显式 v4 GET 不能因响应头丢失而降级成 legacy。 */
export function documentResponseFormat(header: string | null, expected: DocumentFormat = 'legacy'): DocumentFormat | null {
  if (expected === 'v4') return header === 'v4' ? 'v4' : null
  return header === null || header === 'legacy' ? 'legacy' : null
}

export interface VersionCanvasIdentity {
  project: ProjectDocument['project']
  canvasId: string
  createdAt: number
  updatedAt: number
}

/** 时间线仍只拍捕获的活动画布；不夹带别的画布，也不现读 store。 */
export function chartVersionProject(doc: FigureDocument, identity: VersionCanvasIdentity): ChartProjectDocument {
  return structuredClone({
    schema: CHART_DOCUMENT_SCHEMA,
    project: identity.project,
    canvases: [docToCanvas(doc, identity.canvasId)],
    activeCanvasId: identity.canvasId,
    createdAt: identity.createdAt,
    updatedAt: identity.updatedAt,
  })
}

/** v4 节点只允许单 canvas 投影，绝不从多画布项目猜一个恢复对象。 */
export function chartVersionCanvas(raw: unknown): FigureDocument | null {
  if (!raw || typeof raw !== 'object') return null
  const document = raw as Partial<ChartProjectDocument>
  if (document.schema !== CHART_DOCUMENT_SCHEMA || !Array.isArray(document.canvases) || document.canvases.length !== 1) return null
  const canvas = document.canvases[0]
  if (!canvas || typeof canvas !== 'object' || !Array.isArray(canvas.objects) || !canvas.page || typeof canvas.page !== 'object') return null
  return structuredClone(canvasToDoc(canvas as CanvasData))
}
