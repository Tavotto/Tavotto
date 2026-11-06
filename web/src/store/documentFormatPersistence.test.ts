import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { readProjectFile } from '@/lib/projectFile'
import { documentNamespaceKey } from '@/lib/documentFormat'
import { canvasToDoc, emptyProject, type PanelChartState, type PanelObject, type ProjectDocument, type TextObject } from '@/types/document'
import { captureChartSourceSave, flushAutosave, mergeSavedChartSources, probeDocument, setProjectFile, startAutosave, useDocumentStore } from './documentStore'
import { resetRestoreStateForTests } from './documentRestore'
import type { ChartSourceReceipt } from '@/lib/chartSourceReceipts'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const chart = (): PanelChartState => {
  const value = chartVectors.vectors.find(v => v.expect === 'ok')!
  return structuredClone({ model: value.model, binding: value.binding }) as unknown as PanelChartState
}
const panel = (fileId = 'input.svg'): PanelObject => ({ id: 'p', type: 'panel', fileId, fileKind: 'svg',
  nativeW: 100, nativeH: 80, x: 0, y: 0, w: 100, h: 80, chart: chart(), overrides: [] })
const text = (): TextObject => ({ id: 't', type: 'text', text: 'old', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8 })
const sources = (document: ProjectDocument): ChartSourceReceipt[] => document.canvases.flatMap((canvas, ci) =>
  canvas.objects.flatMap((object, oi) => object.type === 'panel' && object.chart ? [{
    canvas_index: ci, object_index: oi, canvas_id: canvas.id ?? null, panel_id: object.id ?? null,
    chart_source: { schema: 1 as const, sourceId: object.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: object.chart.model.scene_digest },
  }] : []))

let stop = () => {}
let sequence = 0
const slots = new Map<string, string>()
const puts: { path: string; body: ProjectDocument }[] = []
beforeEach(async () => {
  vi.useFakeTimers()
  slots.clear(); puts.length = 0; localStorage.clear()
  setCurrentProjectId('project')
  vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(url), 'http://localhost').pathname
    if (!/\/autosave\/[^/]+$/.test(path)) return new Response('{}', { status: 404 })
    if (init?.method === 'PUT') {
      const body = JSON.parse(String(init.body)) as ProjectDocument
      puts.push({ path, body: structuredClone(body) })
      const refs = path.startsWith('/api/documents/v4/') ? sources(body) : undefined
      for (const ref of refs ?? []) (body.canvases[ref.canvas_index].objects[ref.object_index] as PanelObject).chartSource = ref.chart_source
      slots.set(path, JSON.stringify(body))
      return Response.json({ ok: true, revision: `r${puts.length}`, saved_at: 1,
        ...(refs ? { document_format: 'v4', chart_sources: refs } : {}) })
    }
    const body = slots.get(path)
    return new Response(body ?? '{}', { status: body ? 200 : 404,
      headers: body ? { 'X-Tavotto-Revision': 'r', ...(path.startsWith('/api/documents/v4/') ? { 'X-Tavotto-Document-Format': 'v4' } : {}) } : {} })
  }))
  resetRestoreStateForTests()
  await useDocumentStore.getState().switchDocument(emptyProject(), `format_${++sequence}`)
  puts.length = 0
})
afterEach(() => {
  stop(); stop = () => {}
  vi.useRealTimers(); vi.unstubAllGlobals()
})
const settle = () => vi.advanceTimersByTimeAsync(1200)

describe('一个documentStore中的持久化格式', () => {
  it('首次chart提交原子创建v4工作副本，当前undo和旧绑定/文件历史保留，undo不降格式', async () => {
    stop = startAutosave()
    const oldId = useDocumentStore.getState().documentId
    useDocumentStore.getState().commit(literal('old text'), doc => { doc.objects.push(text()) })
    await settle()
    const oldBody = slots.get(`/api/autosave/${oldId}`)
    const binding = { projectId: 'project', name: 'Old', file: 'tavottofile/Old.json', revision: 'old-r', dirty: false }
    setProjectFile(binding)
    const before = useDocumentStore.getState(), oldCanvas = before.activeCanvasId
    useDocumentStore.getState().commit(literal('bind chart'), doc => { doc.objects.push(panel()) })
    const current = useDocumentStore.getState()
    expect(current.documentFormat).toBe('v4')
    expect(current.documentId).not.toBe(oldId)
    expect(current.loadSeq).toBe(before.loadSeq)
    expect(current.projectMeta).toBe(before.projectMeta)
    expect(current.activeCanvasId).toBe(oldCanvas)
    expect(current.canvasSessions).toBe(before.canvasSessions)
    expect(current.past).toHaveLength(2)
    expect(current.projectFile).toBeNull()
    expect(readProjectFile(oldId)).toEqual(binding)
    expect(slots.get(`/api/autosave/${oldId}`)).toBe(oldBody)
    const newId = current.documentId
    current.undo()
    expect(useDocumentStore.getState().doc.objects.map(o => o.type)).toEqual(['text'])
    expect(useDocumentStore.getState().documentId).toBe(newId)
    expect(useDocumentStore.getState().documentFormat).toBe('v4')
    useDocumentStore.getState().redo()
    await settle()
    expect(puts.at(-1)?.path).toBe(`/api/documents/v4/autosave/${newId}`)
    expect(puts.at(-1)?.body.schema).toBe(4)
    expect(slots.get(`/api/autosave/${oldId}`)).toBe(oldBody)
  })

  it('首次chart事务跨防抖和显式flush也不先写legacy，discard维持旧身份和历史', async () => {
    stop = startAutosave()
    const before = useDocumentStore.getState(), past = before.past
    before.beginTxn(literal('draft'))
    useDocumentStore.getState().txnUpdate(doc => { doc.objects.push(panel()) })
    await settle()
    expect(flushAutosave()).toBe('skipped')
    expect(puts).toEqual([])
    expect(localStorage.getItem(`tavotto.autosave.${before.documentId}`)).toBeNull()
    expect(useDocumentStore.getState().documentFormat).toBe('legacy')
    useDocumentStore.getState().endTxn({ discard: true })
    expect(useDocumentStore.getState().documentId).toBe(before.documentId)
    expect(useDocumentStore.getState().documentFormat).toBe('legacy')
    expect(useDocumentStore.getState().past).toBe(past)
    expect(useDocumentStore.getState().doc.objects).toEqual([])
  })

  it('首次chart事务成功后自动保存进入v4，来源回执不加undo或造成循环保存', async () => {
    stop = startAutosave()
    const id = useDocumentStore.getState().documentId
    useDocumentStore.getState().beginTxn(literal('bind'))
    useDocumentStore.getState().txnUpdate(doc => { doc.objects.push(panel()) })
    useDocumentStore.getState().endTxn()
    const current = useDocumentStore.getState()
    expect(current.documentId).not.toBe(id)
    expect(current.past).toHaveLength(1)
    await settle()
    expect(puts).toHaveLength(1)
    expect(puts[0].path).toContain('/api/documents/v4/autosave/')
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).chartSource?.sourceId).toBe('input.svg')
    expect(useDocumentStore.getState().past).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(puts).toHaveLength(1)
    expect(localStorage.getItem(documentNamespaceKey(`tavotto.autosave.${current.documentId}`, 'v4'))).toBeNull()
  })

  it('同docId的legacy/v4正文、probe及writer基线独立', async () => {
    const id = `same_${++sequence}`, legacy = emptyProject(); legacy.canvases[0].objects = [text()]
    slots.set(`/api/autosave/${id}`, JSON.stringify(legacy))
    const project: ProjectDocument = { ...emptyProject(), schema: 4 }
    project.canvases[0].objects = [panel()]
    await useDocumentStore.getState().switchDocument(project, id)
    await settle()
    expect(JSON.parse(slots.get(`/api/autosave/${id}`)!)).toEqual(legacy)
    expect((await probeDocument(id, 'project')).doc?.schema).toBe(3)
    const v4 = await probeDocument(id, 'project', 'v4')
    expect(v4.format).toBe('v4'); expect(v4.doc?.schema).toBe(4)
  })

  it('位置回执只同步实际安装的首个重复canvas及对应非active位置，不触发保存', async () => {
    const project = emptyProject()
    project.schema = 4
    const first = project.canvases[0]
    project.canvases = [{ ...first, id: 'same', objects: [panel('a.svg'), panel('b.svg')] },
      { ...structuredClone(first), id: 'same', objects: [panel('c.svg'), panel('d.svg')] }]
    useDocumentStore.setState({ documentFormat: 'v4', documentId: `receipts_${++sequence}`, canvases: project.canvases,
      activeCanvasId: 'same', doc: canvasToDoc(project.canvases[0]), past: [], future: [], dirty: false })
    stop = startAutosave()
    const state = useDocumentStore.getState(), captured = captureChartSourceSave(state.buildProject(), 'project')
    mergeSavedChartSources(captured, sources(captured.document))
    const current = useDocumentStore.getState()
    expect((current.doc.objects[0] as PanelObject).chartSource?.sourceId).toBe('a.svg')
    expect(current.canvases.map(c => c.objects.map(o => (o as PanelObject).chartSource?.sourceId))).toEqual([
      ['a.svg', 'b.svg'], ['c.svg', 'd.svg'],
    ])
    expect(current.past).toBe(state.past)
    expect(current.snapshotSeq).toBe(state.snapshotSeq + 1)
    expect(current.dirty).toBe(false)
    await settle(); expect(puts).toEqual([])
  })
})
