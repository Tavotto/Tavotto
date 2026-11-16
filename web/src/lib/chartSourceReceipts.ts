import type { ChartSourceRef, PanelObject } from '@/types/document'
import type { DocumentFormat, PersistedProjectDocument } from './documentFormat'
import { sameRules } from './specBinding'

export interface ChartSourceReceipt {
  canvas_index: number
  object_index: number
  canvas_id: unknown
  panel_id: unknown
  chart_source: ChartSourceRef
}

export interface ChartSourceSaveScope {
  format: DocumentFormat
  pj: string | null
  documentId: string
  loadSeq: number
}

export interface ChartSourceSaveContext extends ChartSourceSaveScope {
  /** 调用方必须发送这一份正文，不能重新拍第二份。 */
  document: PersistedProjectDocument
}

export interface ChartSourceUpdate {
  canvasIndex: number
  objectIndex: number
  source: ChartSourceRef
}

export type ChartSourceMergePlan = {
  status: 'absent' | 'invalid' | 'stale' | 'ready'
  updates: ChartSourceUpdate[]
}

const RECEIPT_KEYS = ['canvas_index', 'object_index', 'canvas_id', 'panel_id', 'chart_source']
const SOURCE_KEYS = ['schema', 'sourceId', 'bytesSha256', 'sizeBytes', 'sceneDigest']
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const exactKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const sha256 = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

/** 只解码回执 wire；预算、附件存在/摘要与真实重放资格仍由后端判定。 */
function sourceShape(value: unknown): value is ChartSourceRef {
  return record(value) && exactKeys(value, SOURCE_KEYS) && value.schema === 1
    && typeof value.sourceId === 'string' && !!value.sourceId.trim() && !value.sourceId.includes('\0')
    && sha256(value.bytesSha256) && sha256(value.sceneDigest)
    && typeof value.sizeBytes === 'number' && Number.isSafeInteger(value.sizeBytes) && value.sizeBytes > 0
}

function parseReceipts(raw: unknown): ChartSourceReceipt[] | null {
  if (!Array.isArray(raw)) return null
  const positions = new Set<string>()
  for (const receipt of raw) {
    if (!record(receipt) || !exactKeys(receipt, RECEIPT_KEYS)
      || !Number.isSafeInteger(receipt.canvas_index) || (receipt.canvas_index as number) < 0
      || !Number.isSafeInteger(receipt.object_index) || (receipt.object_index as number) < 0
      || !sourceShape(receipt.chart_source)) return null
    const key = `${receipt.canvas_index}:${receipt.object_index}`
    if (positions.has(key)) return null
    positions.add(key)
  }
  return raw as ChartSourceReceipt[]
}

const idOf = (object: { id?: unknown }) => object.id ?? null
const inputOf = (panel: PanelObject) => ({
  fileId: panel.fileId, fileKind: panel.fileKind, chart: panel.chart,
  overrides: panel.overrides, chartSource: panel.chartSource,
})

export function captureChartSourceSave(document: PersistedProjectDocument, scope: ChartSourceSaveScope): ChartSourceSaveContext {
  return { ...scope, document: structuredClone(document) }
}

/**
 * 只计划位置更新，不写 store。正文 I/O 成功与回执可合入是两件事；invalid 不触发覆盖重试。
 * 当前正文须来自同一次 buildProject，活动画布的实际首个安装位置由调用方保持。
 */
export function planSavedChartSources(
  context: ChartSourceSaveContext,
  currentDocument: PersistedProjectDocument,
  currentScope: ChartSourceSaveScope,
  rawReceipts: unknown,
): ChartSourceMergePlan {
  if (rawReceipts === undefined) return { status: 'absent', updates: [] }
  if (currentScope.format !== context.format || currentScope.pj !== context.pj
    || currentScope.documentId !== context.documentId || currentScope.loadSeq !== context.loadSeq) {
    return { status: 'stale', updates: [] }
  }
  if (context.format !== 'v4' || context.document.schema !== 4 || currentDocument.schema !== 4) return { status: 'invalid', updates: [] }
  const receipts = parseReceipts(rawReceipts)
  if (!receipts) return { status: 'invalid', updates: [] }
  const updates: ChartSourceUpdate[] = []
  for (const receipt of receipts) {
    const sentCanvas = context.document.canvases[receipt.canvas_index]
    const sent = sentCanvas?.objects?.[receipt.object_index]
    if (!sentCanvas || sent?.type !== 'panel' || !sent.chart
      || !sameRules(idOf(sentCanvas), receipt.canvas_id) || !sameRules(idOf(sent), receipt.panel_id)
      || receipt.chart_source.sourceId !== sent.fileId || receipt.chart_source.sceneDigest !== sent.chart.model.scene_digest) {
      return { status: 'invalid', updates: [] }
    }
    const currentCanvas = currentDocument.canvases[receipt.canvas_index]
    const panel = currentCanvas?.objects?.[receipt.object_index]
    if (!currentCanvas || panel?.type !== 'panel'
      || !sameRules(idOf(currentCanvas), receipt.canvas_id) || !sameRules(idOf(panel), receipt.panel_id)
      || !sameRules(inputOf(panel), inputOf(sent))) continue
    if (!sameRules(panel.chartSource, receipt.chart_source)) {
      updates.push({ canvasIndex: receipt.canvas_index, objectIndex: receipt.object_index, source: structuredClone(receipt.chart_source) })
    }
  }
  return { status: 'ready', updates }
}
