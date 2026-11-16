import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import { setCurrentProjectId } from '@/lib/session'
import { readProjectFile } from '@/lib/projectFile'
import { captureSaveContext, stillCurrent } from './saveContext'
import { setProjectFile, startAutosave, useDocumentStore } from './documentStore'
import { writeBoundProjectFile } from './projectSave'
import { resetRestoreStateForTests } from './documentRestore'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

let release: (() => void) | undefined
let hold: Promise<void>
const sent: ProjectDocument[] = []
let seq = 0
let stop = () => {}
beforeEach(async () => {
  localStorage.clear(); sent.length = 0; setCurrentProjectId('p_save4'); resetRestoreStateForTests()
  hold = new Promise<void>(r => { release = r })
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url), 'http://localhost').pathname
    if (path.includes('/layouts/') && init?.method === 'POST') {
      const pd = JSON.parse(String(init.body)) as ProjectDocument; sent.push(pd); await hold
      const p = pd.canvases[0].objects[0] as PanelObject
      return Response.json({ ok: true, name: 'Named', file: 'tavottofile/documents/v4/Named.json', revision: 'r4', document_format: 'v4', chart_sources: [{ canvas_index: 0, object_index: 0, canvas_id: pd.canvases[0].id, panel_id: p.id, chart_source: { schema: 1, sourceId: p.fileId, sceneDigest: p.chart!.model.scene_digest, bytesSha256: 'a'.repeat(64), sizeBytes: 100 } }] })
    }
    if (init?.method === 'PUT') return Response.json({ ok: true, revision: 'auto-r', saved_at: 1 })
    return new Response('{}', { status: 404 })
  }))
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  const p: PanelObject = { id: 'p', type: 'panel', fileId: 'gone.svg', fileKind: 'svg', x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80, chart: structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelObject['chart'], overrides: [{ gid: 'axes_0.lines_0', prop: 'linewidth', value: 2 }] }
  const pd = emptyProject(); pd.schema = 4; pd.canvases[0].objects = [p]
  await useDocumentStore.getState().switchDocument(pd, `save4_${++seq}`)
  stop = startAutosave()
  setProjectFile({ projectId: 'p_save4', name: 'Named', file: 'tavottofile/documents/v4/Named.json', revision: 'r0', dirty: true })
})
afterEach(() => { stop(); release?.(); vi.unstubAllGlobals(); setCurrentProjectId(null) })
const currentPanel = () => useDocumentStore.getState().doc.objects[0] as PanelObject

describe('v4命名保存回执实际发送输入与当前文档同版', () => {
  it('保存发送schema4且正确回执只走snapshotSeq，不多一条撤销', async () => {
    const before = useDocumentStore.getState(), ctx = captureSaveContext()
    const pending = writeBoundProjectFile(ctx)
    await vi.waitFor(() => expect(sent).toHaveLength(1)); expect(sent[0].schema).toBe(4)
    release!(); expect(await pending).toBe(true)
    expect(currentPanel().chartSource?.sourceId).toBe('gone.svg')
    expect(useDocumentStore.getState().past).toBe(before.past)
    expect(useDocumentStore.getState().snapshotSeq).toBe(before.snapshotSeq + 1)
  })
  it('期间用户改edits：旧回执不能贴到新计算输入，已有编辑/dirty保留', async () => {
    const ctx = captureSaveContext(), pending = writeBoundProjectFile(ctx)
    await vi.waitFor(() => expect(sent).toHaveLength(1))
    useDocumentStore.getState().commit(literal('linewidth'), d => { (d.objects[0] as PanelObject).overrides[0].value = 6 })
    release!(); expect(await pending).toBe(true)
    expect(currentPanel().chartSource).toBeUndefined(); expect(currentPanel().overrides[0].value).toBe(6)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(readProjectFile(ctx.documentId, 'v4')?.dirty).toBe(true)
  })
  it('同docId切到legacy且load不同，旧v4回执和绑定只归原namespace', async () => {
    const ctx = captureSaveContext(), pending = writeBoundProjectFile(ctx)
    await vi.waitFor(() => expect(sent).toHaveLength(1))
    await useDocumentStore.getState().switchDocument(emptyProject(), ctx.documentId)
    expect(stillCurrent(ctx)).toBe(false)
    release!(); expect(await pending).toBe(true)
    expect(useDocumentStore.getState().documentFormat).toBe('legacy')
    expect(useDocumentStore.getState().doc.objects).toEqual([])
    expect(useDocumentStore.getState().projectFile).toBeNull()
    expect(readProjectFile(ctx.documentId, 'v4')?.revision).toBe('r4')
    expect(readProjectFile(ctx.documentId)).toBeNull()
  })
})
