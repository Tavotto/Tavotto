import { describe, expect, it } from 'vitest'
import { captureChartSourceSave, planSavedChartSources, type ChartSourceSaveScope } from './chartSourceReceipts'
import type { ChartSourceRef, PanelChartState, PanelObject, ProjectDocument } from '@/types/document'

const DIGEST = 'b'.repeat(64)

const source = (over: Partial<ChartSourceRef> = {}): ChartSourceRef => ({
  schema: 1, sourceId: 'input.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 10, sceneDigest: DIGEST,
  ...over,
})

const chart = { model: { scene_digest: DIGEST }, binding: {} } as unknown as PanelChartState

const panel = (state: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1', type: 'panel', fileId: 'input.svg', fileKind: 'svg',
  nativeW: 10, nativeH: 10, x: 0, y: 0, w: 10, h: 10,
  overrides: [], chart,
  ...state,
})

const doc = (state: Partial<PanelObject> = {}, schema: 3 | 4 = 4): ProjectDocument => ({
  schema,
  project: { id: 'pj', name: 'n' },
  canvases: [{ id: 'c1', name: 'Fig 1', page: { w: 100, h: 80 }, objects: [panel(state)], guides: [] }],
  activeCanvasId: 'c1',
  createdAt: 0,
  updatedAt: 1,
})

const scope = (over: Partial<ChartSourceSaveScope> = {}): ChartSourceSaveScope => ({
  pj: 'pj', documentId: 'd1', loadSeq: 1, ...over,
})

const receipt = (chartSource = source()) => ({
  canvas_index: 0, object_index: 0, canvas_id: 'c1', panel_id: 'p1', chart_source: chartSource,
})

describe('planSavedChartSources：单一存储后只按 pj + documentId + loadSeq 认同一份', () => {
  it('捕获保存正文是快照：之后改原对象不影响发给后端的那一份', () => {
    const document = doc()
    const captured = captureChartSourceSave(document, scope())
    const panel = document.canvases[0].objects[0] as PanelObject
    panel.chartSource = undefined
    expect((captured.document.canvases[0].objects[0] as PanelObject).chartSource).toBeUndefined()
    expect(captured.pj).toBe('pj')
  })

  it('没有回执 = absent；项目 / 文档 / 载入代次对不上 = stale', () => {
    const document = doc()
    const context = captureChartSourceSave(document, scope())
    expect(planSavedChartSources(context, document, scope(), undefined)).toEqual({ status: 'absent', updates: [] })
    for (const current of [scope({ pj: 'other' }), scope({ documentId: 'd2' }), scope({ loadSeq: 2 })]) {
      expect(planSavedChartSources(context, document, current, [])).toEqual({ status: 'stale', updates: [] })
    }
  })

  it('schema 3 的正文 / 回执不是 schema 4 都判 invalid（不再有 format 轴）', () => {
    const document = doc()
    const context = captureChartSourceSave(document, scope())
    expect(planSavedChartSources(context, doc({}, 3), scope(), [])).toEqual({ status: 'invalid', updates: [] })
    expect(planSavedChartSources({ ...context, document: doc({}, 3) }, doc({}, 3), scope(), [])).toEqual({ status: 'invalid', updates: [] })
    expect(planSavedChartSources(context, document, scope(), [{ ...receipt(), chart_source: null }])).toEqual({ status: 'invalid', updates: [] })
  })

  it('正文里还没有 chartSource 时按回执补上；已有同一份就什么都不写', () => {
    const sent = doc()
    const context = captureChartSourceSave(sent, scope())
    const current = doc()
    expect(planSavedChartSources(context, current, scope(), [receipt()])).toEqual({
      status: 'ready',
      updates: [{ canvasIndex: 0, objectIndex: 0, source: source() }],
    })
    const withSource = doc({ chartSource: source() })
    expect(planSavedChartSources(context, withSource, scope(), [receipt()])).toEqual({ status: 'ready', updates: [] })
  })

  it('回执与发送正文/当前位置对不上（素材、摘要、画布或面板 id）一律 invalid', () => {
    const context = captureChartSourceSave(doc(), scope())
    const current = doc()
    for (const raw of [
      [receipt(source({ sourceId: 'other.svg' }))],
      [receipt(source({ sceneDigest: 'other' }))],
      [{ ...receipt(), canvas_id: 'c2' }],
      [{ ...receipt(), panel_id: 'p2' }],
    ]) {
      expect(planSavedChartSources(context, current, scope(), raw)).toEqual({ status: 'invalid', updates: [] })
    }
  })
})
