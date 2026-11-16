import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { setEngineTransport } from '@/lib/engineTransport'
import { installMomentSources, type ThumbSources } from '@/lib/momentCapture'
import { projectFileEditor, type ProjectFileAxis, type ProjectFileView } from '@/lib/projectFileSession'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { emptyProject, type FigureDocument, type PanelObject, type ProjectDocument } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import { resetRestoreStateForTests } from './documentRestore'
import { getRestoreGate, overwriteDisk, prepareProjectFileSave, reloadFromDisk, saveNow, useDocumentStore, waitForWorkingDocumentSave } from './documentStore'
import { useProjectStore } from './projectStore'
import { captureThumbSources } from './renderStore'

type Request = { path: string; method: string; pj: string | null; header: string | null; url: URL; body: ProjectDocument & Record<string, unknown>; current: string | null; documentId: string }
type Slot = { document: ProjectDocument; revision: string; axis: ProjectFileAxis }
const A = 'project-A', B = 'project-B'
const state = () => useDocumentStore.getState()
const requests: Request[] = []
const slots = new Map<string, Slot>()
const captures: { doc: FigureDocument; pj: string | null; sources: ThumbSources }[] = []
const blobs = new Map<string, Blob>()
const pendingPreviews: (() => void)[] = []
let id: string, serial = 0, revision = 0, holdPreviews = false, rejectOrdinaryWrite = false
const scope = () => ({ pj: A, documentId: id, format: 'v4' as const, loadSeq: state().loadSeq })
const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve() }
const puts = () => requests.filter(r => r.method === 'PUT' && r.path === `/api/autosave/${id}`)
const previewRequests = () => requests.filter(r => r.path === '/api/engine/preview_png')
const metadata = (slot?: Slot): ProjectFileView => ({ version: 1, status: 'ok', binding: null, intent_sequence: 0,
  edit: { known: true, axis: slot?.axis ?? null, autosave_revision: slot?.revision ?? null } })
const projectStatus = (pj: string) => ({ open: true, id: pj, name: pj, figures_dir: `/owned/${pj}` })

function documentWithPanels(): ProjectDocument {
  const document = emptyProject(); document.schema = 4
  const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
  const chart = structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelObject['chart']
  const base = { type: 'panel' as const, x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80, figureFrame: 1 as const, overrides: [] }
  const historical = (fileId: string, fileKind: 'pdf' | 'runtime'): PanelObject => ({ ...base, id: fileKind, fileId, fileKind,
    figureSnapshot: { version: 1, sourceId: fileId, patchHash: 'same-patches', receiptId: 'receipt-A', snapshotId: `archive-${fileKind}`,
      executionOwner: { script: 'plot.py', run_config: null } } })
  document.canvases[0].objects = [
    { ...base, id: 'chart', fileId: 'chart.svg', fileKind: 'svg', chart,
      chartSource: { schema: 1, sourceId: 'chart.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 100, sceneDigest: chart!.model.scene_digest } },
    historical('History.pdf', 'pdf'), historical('runtime:plot.py#History', 'runtime'),
    { ...base, id: 'ordinary', fileId: 'same.svg', fileKind: 'svg' },
  ]
  return document
}

beforeEach(async () => {
  setEngineTransport(null)
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test')
    if (url.protocol === 'blob:') return { blob: async () => blobs.get(String(input))! } as Response
    const path = url.pathname.replace('/api/documents/v4/', '/api/'), v4 = url.pathname.includes('/documents/v4/')
    const method = init?.method ?? 'GET', pj = url.searchParams.get('pj')
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null
    requests.push({ path, method, pj, header: new Headers(init?.headers).get('X-Tavotto-Project'), url, body,
      current: currentProjectId(), documentId: state().documentId })
    const reply = (value: unknown, status = 200, rev?: string) => Response.json(value, {
      status, headers: { ...(v4 ? { 'X-Tavotto-Document-Format': 'v4' } : {}), ...(rev ? { 'X-Tavotto-Revision': rev } : {}) },
    })
    if (path === '/api/engine/preview_png') {
      if (holdPreviews) await new Promise<void>(resolve => pendingPreviews.push(resolve))
      if (body.figure_snapshot) {
        const response = new Response('', { headers: { 'Content-Type': 'image/png', 'X-Tavotto-Figure-Snapshot': JSON.stringify(body.figure_snapshot) } })
        // jsdom FileReader expects its own realm's Blob; use the response's frozen request identity.
        response.blob = async () => new Blob([`${pj}:${body.id}`], { type: 'image/png' })
        return response
      }
      return reply({ png: btoa(`${pj}:${body.id}`), chart_revision: 'paired-chart', manifest: { stem: 'chart', size_mm: [100, 80], elements: [] } })
    }
    const address = path.match(/^\/api\/layout-session\/project-file\/([^/]+)$/)
    if (address) return reply(metadata(slots.get(address[1])))
    const auto = path.match(/^\/api\/autosave\/([^/]+)$/)
    if (auto) {
      const target = auto[1], slot = slots.get(target)
      if (method === 'GET') return slot ? reply(slot.document, 200, slot.revision) : reply({}, 404)
      if (method === 'PUT') {
        const edit = JSON.parse(url.searchParams.get('project_file_edit') ?? 'null')
        const proof = JSON.parse(url.searchParams.get('project_file_recovery') ?? 'null')
        if (proof) {
          expect(proof).toEqual({ revision: slot!.revision, expected: metadata(slot) })
          expect(edit.axis).not.toEqual(slot!.axis)
        } else if (rejectOrdinaryWrite) return reply({ code: 'external_change', revision: slot!.revision }, 409)
        const saved = { document: body, revision: (++revision).toString(16).padStart(32, '0'), axis: edit?.axis ?? null }
        slots.set(target, saved)
        return reply({ ok: true, saved_at: Date.now(), revision: saved.revision, project_file: { state: 'accepted', ...metadata(saved) } })
      }
    }
    if (path.endsWith('/reserve')) return reply({ state: 'reserved', ticket: { sequence: 1, binding_sequence: 0,
      name: body.name, file: `tavottofile/documents/v4/${body.name}.json`, autosave_revision: body.autosave_revision, axis: body.axis } })
    if (path.startsWith('/api/projects')) return reply({ projects: [], recent: [], pinned: [] })
    if (path.startsWith('/api/preferences/')) return reply({ defaults: null })
    if (path === '/api/panels') return reply([])
    return reply({}, 404)
  }))
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const url = `blob:owner-${blobs.size}`; blobs.set(url, blob as Blob); return url })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  holdPreviews = false; rejectOrdinaryWrite = false
  setCurrentProjectId(null); resetRestoreStateForTests()
  await state().switchDocument(emptyProject(), `owner-reset-${++serial}`)
  await settle()
  projectFileEditor.clear(); localStorage.clear(); slots.clear(); requests.length = 0; captures.length = 0
  id = `owner-${serial}`
  // Observe the original synchronous port without replacing renderer or transport behavior.
  installMomentSources((doc, pj) => {
    const sources = captureThumbSources(doc, pj)
    captures.push({ doc, pj, sources })
    return sources
  })
  setCurrentProjectId(A)
  useProjectStore.setState({ phase: 'open', project: projectStatus(A) })
})

afterEach(async () => {
  holdPreviews = false; rejectOrdinaryWrite = false
  pendingPreviews.splice(0).forEach(resolve => resolve())
  await Promise.all(captures.flatMap(capture => [...(capture.sources?.values() ?? [])].map(source => source?.png)))
  await settle()
  installMomentSources(captureThumbSources)
  setCurrentProjectId(null); resetRestoreStateForTests()
  await state().switchDocument(emptyProject(), `owner-cleanup-${serial}`)
  await settle(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

async function install(document = documentWithPanels()) {
  document.schema = 4
  expect(await state().switchDocument(document, id, undefined, { format: 'v4' })).toBe(true)
  await settle()
  return scope()
}

describe('保存Moment与同次writer地址同源', () => {
  it('原项目切换链：current B + 正文 A，三类PNG与普通素材仍归A；原PUT正文不变', async () => {
    await waitForWorkingDocumentSave(await install())
    requests.length = 0; captures.length = 0
    vi.spyOn(Date, 'now').mockReturnValue(Date.now())
    const original = structuredClone(state().buildProject())
    await useProjectStore.getState().adoptOpenedProject(projectStatus(B), {
      prepareDocument: () => state().switchDocument(emptyProject(), 'target-B'),
    })
    await settle()
    expect(getRestoreGate()).toMatchObject({ state: 'ready', pj: B, reason: 'prepared' })
    expect(previewRequests()).toHaveLength(3)
    expect(previewRequests().map(r => r.body.id)).toEqual(['chart.svg', 'History.pdf', 'runtime:plot.py#History'])
    expect(previewRequests().map(({ pj, header, current, documentId }) => ({ pj, header, current, documentId })))
      .toEqual(Array.from({ length: 3 }, () => ({ pj: A, header: A, current: B, documentId: id })))
    expect(puts()).toHaveLength(1)
    expect(puts()[0]).toMatchObject({ pj: A, header: A })
    expect(puts()[0].body).toEqual(original)
    expect(captures).toHaveLength(1)
    expect(new URL(captures[0].sources!.get('ordinary')!.url!, 'http://test').searchParams.get('pj')).toBe(A)
  })

  it('离开writer的原PNG Promise迟到到A→B→A之后仍完成原A输入，不取消或借新稿', async () => {
    await waitForWorkingDocumentSave(await install())
    requests.length = 0; captures.length = 0; holdPreviews = true
    await useProjectStore.getState().adoptOpenedProject(projectStatus(B), {
      prepareDocument: () => state().switchDocument(emptyProject(), 'late-B'),
    })
    expect(getRestoreGate()).toMatchObject({ state: 'ready', pj: B, reason: 'prepared' })
    const leaving = captures[0], promises = ['chart', 'pdf', 'runtime'].map(key => leaving.sources!.get(key)!.png!)
    expect(promises.every(promise => promise instanceof Promise)).toBe(true)
    await useProjectStore.getState().adoptOpenedProject(projectStatus(A), {
      prepareDocument: () => state().switchDocument(emptyProject(), 'new-A'),
    })
    expect(getRestoreGate()).toMatchObject({ state: 'ready', pj: A, reason: 'prepared' })
    expect(state().documentId).toBe('new-A')
    expect(pendingPreviews).toHaveLength(3)
    pendingPreviews.splice(0).forEach(resolve => resolve())
    expect(await Promise.all(promises)).toEqual(['chart.svg', 'History.pdf', 'runtime:plot.py#History'].map(file => `data:image/png;base64,${btoa(`${A}:${file}`)}`))
    expect(captures[0]).toBe(leaving)
    expect(previewRequests()).toHaveLength(3)
    expect(previewRequests().every(r => r.pj === A && r.header === A)).toBe(true)
  })

  it.each(['persistEmpty', 'named'] as const)('%s空稿仍走原一次writer，回执Moment身份与保存地址一致', async action => {
    const installed = await install(emptyProject())
    expect(puts()).toHaveLength(0)
    const receipt = action === 'persistEmpty'
      ? await waitForWorkingDocumentSave(installed, { persistEmpty: true })
      : await prepareProjectFileSave('Empty', installed)
    expect(receipt).not.toBeNull()
    expect(puts()).toHaveLength(1)
    expect(receipt!.address).toEqual({ projectId: A, format: 'v4', documentId: id })
    expect(receipt!.moment!.identity).toMatchObject({ pj: A, documentId: id, format: 'v4', doc: state().doc })
    expect(receipt!.moment!.thumb?.size).toBe(0)
    expect(requests.filter(r => r.path.endsWith('/reserve'))).toHaveLength(action === 'named' ? 1 : 0)
  })

  it.each(['overwrite', 'reload'] as const)('%s冲突裁决仍有原proof，新writer与Moment取同一A地址及裁决正文', async action => {
    await waitForWorkingDocumentSave(await install())
    state().commit(literal('edit'), doc => { doc.guides.push({ axis: 'x', pos: 11 }) })
    const external = slots.get(id)!
    external.document.project.name = 'external copy'
    external.revision = 'e'.repeat(32)
    external.axis = { editor_id: 'other-browser', sequence: 9 }
    rejectOrdinaryWrite = true
    expect(await saveNow()).toBe('conflict')
    requests.length = 0; captures.length = 0
    expect(await (action === 'reload' ? reloadFromDisk() : overwriteDisk())).toBe(action === 'reload' ? true : 'saved')
    expect(puts()).toHaveLength(1)
    expect(puts()[0].url.searchParams.has('project_file_recovery')).toBe(true)
    expect(puts()[0].pj).toBe(A)
    expect(puts()[0].body.canvases[0].guides).toEqual(action === 'reload' ? [] : [{ axis: 'x', pos: 11 }])
    const receipt = await waitForWorkingDocumentSave(scope())
    expect(receipt.moment!.identity).toMatchObject({ pj: A, documentId: id, doc: state().doc })
    expect(receipt.document).toEqual(slots.get(id)!.document)
    expect(previewRequests()).toHaveLength(3)
    expect(previewRequests().every(r => r.pj === receipt.address.projectId)).toBe(true)
  })
})
