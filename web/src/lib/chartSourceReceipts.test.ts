import { describe, expect, it } from 'vitest'
import { captureChartSourceSave, planSavedChartSources, type ChartSourceReceipt, type ChartSourceSaveScope } from './chartSourceReceipts'
import type { ChartProjectDocument } from './documentFormat'
import type { CanvasData, ChartSourceRef, PanelChartState, PanelObject } from '@/types/document'
import { emptyProject } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const scope = (): ChartSourceSaveScope => ({ format: 'v4', pj: 'project', documentId: 'working', loadSeq: 3 })
const chart = (): PanelChartState => {
  const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
  return structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelChartState
}
function panel(fileId: string): PanelObject {
  return { id: 'duplicate-panel', type: 'panel', fileId, fileKind: 'svg', nativeW: 100, nativeH: 80,
    x: 0, y: 0, w: 100, h: 80, chart: chart(),
    overrides: [{ gid: 's', prop: 'color', value: 'red' }, { gid: 's', prop: 'color', value: 'blue' }] }
}
function document(): ChartProjectDocument {
  const base = emptyProject(), first = base.canvases[0]
  return { ...base, schema: 4, activeCanvasId: 'duplicate-canvas', canvases: [
    { ...first, id: 'duplicate-canvas', objects: [panel('a.svg'), panel('b.svg')] },
    { ...structuredClone(first), id: 'duplicate-canvas', objects: [panel('c.svg'), panel('d.svg')] },
  ] }
}
const at = (doc: ChartProjectDocument, ci = 0, oi = 0) => doc.canvases[ci].objects[oi] as PanelObject
function receipts(doc: ChartProjectDocument): ChartSourceReceipt[] {
  return doc.canvases.flatMap((canvas, ci) => canvas.objects.map((object, oi) => ({
    canvas_index: ci, object_index: oi, canvas_id: canvas.id ?? null, panel_id: object.id ?? null,
    chart_source: { schema: 1, sourceId: (object as PanelObject).fileId,
      bytesSha256: String(1 + ci * 2 + oi).repeat(64), sizeBytes: 100,
      sceneDigest: (object as PanelObject).chart!.model.scene_digest } as ChartSourceRef,
  })))
}
const freeze = (value: unknown): void => {
  if (value && typeof value === 'object') { Object.freeze(value); for (const item of Object.values(value)) freeze(item) }
}

describe('保存回执以正文位置和完整输入为依据', () => {
  it('同/跨画布重复panelId及重复canvasId四个位置均独立，不用id Map覆盖', () => {
    const sent = document(), raw = receipts(sent), context = captureChartSourceSave(sent, scope())
    const before = structuredClone(sent)
    freeze(sent); freeze(context); freeze(raw)
    const result = planSavedChartSources(context, sent, scope(), raw)
    expect(result.status).toBe('ready')
    expect(result.updates.map(u => [u.canvasIndex, u.objectIndex, u.source.sourceId])).toEqual([
      [0, 0, 'a.svg'], [0, 1, 'b.svg'], [1, 0, 'c.svg'], [1, 1, 'd.svg'],
    ])
    expect(sent).toEqual(before)
    expect(result.updates[0].source).not.toBe(raw[0].chart_source)
  })

  it('context冻结真正要发送的正文；调用前原对象或scope后改不影响捕获', () => {
    const sent = document(), owner = scope(), context = captureChartSourceSave(sent, owner)
    const raw = receipts(sent), current = structuredClone(sent)
    at(sent).fileId = 'later.svg'; owner.documentId = 'other'
    expect(at(context.document as ChartProjectDocument).fileId).toBe('a.svg')
    expect(planSavedChartSources(context, current, scope(), raw).updates).toHaveLength(4)
  })

  it.each(['missing', 'empty', 'null', 'number'] as const)('原始canvas/panel IDs为 %s 不擅改身份也不丢位置', (kind) => {
    const sent = document(), canvas = sent.canvases[0], object = at(sent)
    if (kind === 'missing') { delete (canvas as Partial<CanvasData>).id; delete (object as Partial<PanelObject>).id }
    else {
      const id = kind === 'empty' ? '' : kind === 'null' ? null : 7
      ;(canvas as unknown as { id: unknown }).id = id
      ;(object as unknown as { id: unknown }).id = id
    }
    const result = planSavedChartSources(captureChartSourceSave(sent, scope()), sent, scope(), receipts(sent))
    expect(result.status).toBe('ready'); expect(result.updates).toHaveLength(4)
  })

  it.each([
    ['data', (p: PanelObject) => { p.chart!.model.dataset.columns.extra = [9] }],
    ['range', (p: PanelObject) => { p.chart!.model.axes[0].xlim[1] += 1 }],
    ['file', (p: PanelObject) => { p.fileId = 'replacement.svg' }],
    ['kind', (p: PanelObject) => { p.fileKind = 'pdf' }],
    ['edit-order', (p: PanelObject) => { p.overrides.reverse() }],
    ['old-ref', (p: PanelObject) => { p.chartSource = { schema: 1, sourceId: p.fileId,
      bytesSha256: 'f'.repeat(64), sizeBytes: 50, sceneDigest: p.chart!.model.scene_digest } }],
  ] as const)('%s在途变化只跳过该位置，其他位置照常合入', (_name, change) => {
    const sent = document(), current = structuredClone(sent)
    change(at(current))
    const plan = planSavedChartSources(captureChartSourceSave(sent, scope()), current, scope(), receipts(sent))
    expect(plan.status).toBe('ready')
    expect(plan.updates.map(u => [u.canvasIndex, u.objectIndex])).toEqual([[0, 1], [1, 0], [1, 1]])
  })

  it('画布重排/前插不串；对象重排/前插不串，重复IDs不能掩盖完整输入变化', () => {
    const sent = document(), context = captureChartSourceSave(sent, scope()), raw = receipts(sent)
    const swapped = structuredClone(sent); swapped.canvases.reverse()
    expect(planSavedChartSources(context, swapped, scope(), raw).updates).toEqual([])
    const inserted = structuredClone(sent); inserted.canvases.unshift({ ...structuredClone(sent.canvases[0]), objects: [] })
    expect(planSavedChartSources(context, inserted, scope(), raw).updates).toEqual([])
    const objectsSwapped = structuredClone(sent); objectsSwapped.canvases[0].objects.reverse()
    expect(planSavedChartSources(context, objectsSwapped, scope(), raw).updates.map(u => u.canvasIndex)).toEqual([1, 1])
    const objectsInserted = structuredClone(sent); objectsInserted.canvases[0].objects.unshift(panel('inserted.svg'))
    expect(planSavedChartSources(context, objectsInserted, scope(), raw).updates.map(u => u.canvasIndex)).toEqual([1, 1])
  })

  it('相同路径用完整相同输入替换可安全接收；已相同ref不会再产生写入', () => {
    const sent = document(), raw = receipts(sent), context = captureChartSourceSave(sent, scope())
    const current = structuredClone(sent)
    current.canvases[0].objects[0] = structuredClone(at(sent))
    expect(planSavedChartSources(context, current, scope(), raw).updates).toHaveLength(4)
    const withRefs = structuredClone(sent)
    for (const receipt of raw) at(withRefs, receipt.canvas_index, receipt.object_index).chartSource = receipt.chart_source
    const captured = captureChartSourceSave(withRefs, scope())
    expect(planSavedChartSources(captured, withRefs, scope(), raw)).toEqual({ status: 'ready', updates: [] })
  })

  it.each([
    { pj: 'other' }, { pj: null }, { documentId: 'other' }, { loadSeq: 4 }, { format: 'legacy' as const },
  ])('项目/document/loadSeq/format变化后整份旧回执无提交权 %j', (change) => {
    const sent = document()
    expect(planSavedChartSources(captureChartSourceSave(sent, scope()), sent, { ...scope(), ...change }, receipts(sent)))
      .toEqual({ status: 'stale', updates: [] })
  })

  it('旧响应缺席不改变正文；null/畸形/重复位置整份拒绝，不first/last/部分覆盖', () => {
    const sent = document(), context = captureChartSourceSave(sent, scope()), raw = receipts(sent)
    expect(planSavedChartSources(context, sent, scope(), undefined)).toEqual({ status: 'absent', updates: [] })
    for (const bad of [null, {}, [raw[0], raw[0]], [raw[1], { ...raw[0], extra: true }],
      [raw[1], { ...raw[0], canvas_id: 'wrong' }], [raw[1], { ...raw[0], panel_id: 'wrong' }],
      [raw[1], { ...raw[0], canvas_index: 99 }], [raw[1], { ...raw[0], object_index: 99 }]]) {
      expect(planSavedChartSources(context, sent, scope(), bad)).toEqual({ status: 'invalid', updates: [] })
    }
  })

  it.each([-1, 0.5, Number.MAX_SAFE_INTEGER + 1, '0', true, null])('非法位置 %j 不是有效索引', (index) => {
    const sent = document(), raw = receipts(sent)
    for (const field of ['canvas_index', 'object_index']) {
      expect(planSavedChartSources(captureChartSourceSave(sent, scope()), sent, scope(), [{ ...raw[0], [field]: index }]))
        .toEqual({ status: 'invalid', updates: [] })
    }
  })

  it.each([
    { schema: 2 }, { sourceId: 'elsewhere.svg' }, { sourceId: ' ' }, { sourceId: 'a\0.svg' },
    { sceneDigest: 'f'.repeat(64) }, { bytesSha256: 'not-a-digest' }, { extra: 1 },
    { sizeBytes: 0 }, { sizeBytes: -1 }, { sizeBytes: 0.5 }, { sizeBytes: Infinity }, { sizeBytes: true },
  ])('非法/不匹配引用整份不合入 %j', (change) => {
    const sent = document(), raw = receipts(sent)
    raw[0].chart_source = { ...raw[0].chart_source, ...change } as ChartSourceRef
    expect(planSavedChartSources(captureChartSourceSave(sent, scope()), sent, scope(), raw)).toEqual({ status: 'invalid', updates: [] })
  })

  it('legacy上下文不能消费v4回执；错误schema不因格式字段而获资格', () => {
    const sent = document(), raw = receipts(sent)
    expect(planSavedChartSources(captureChartSourceSave(sent, { ...scope(), format: 'legacy' }), sent,
      { ...scope(), format: 'legacy' }, raw)).toEqual({ status: 'invalid', updates: [] })
    const wrongSchema = { ...sent, schema: 3 as const }
    expect(planSavedChartSources(captureChartSourceSave(wrongSchema, scope()), sent, scope(), raw)).toEqual({ status: 'invalid', updates: [] })
  })
})
