import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject, type ProjectDocument } from '@/types/document'
import { setEngineTransport, type EnginePngSnapshot } from '@/lib/engineTransport'
import type { Manifest } from '@/lib/api'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import { setCurrentProjectId } from '@/lib/session'
import { projectFileEditor } from '@/lib/projectFileSession'
import { captureThumbSources } from '@/lib/timelineThumb'
import { resetRestoreStateForTests } from './documentRestore'
import { hydrateProjectFile, saveNowWithResult, suspendAutosaveFor, useDocumentStore, waitForWorkingDocumentSave, type WorkingDocumentSaveScope } from './documentStore'

vi.mock('@/lib/timelineThumb', () => ({ captureThumbSources: vi.fn(() => new Map()), composeTimelineThumb: vi.fn(async () => null) }))

const s = () => useDocumentStore.getState()
let serial = 0
let id = ''
let selected: 'legacy' | 'v4' = 'v4'
let mode: 'ok' | 'unsupported' | 'unavailable' | 'metadataFailed' = 'ok'
let writeFailure = false
let headerFailure = false
let gate: Promise<void> | null = null
let release: (() => void) | undefined
let getStarted: (() => void) | undefined
const slots = new Map<string, { document: ProjectDocument; revision: string; axis: unknown }>()
const requests: { path: string; method: string; url: URL }[] = []
const hex = (n: number) => n.toString(16).padStart(32, '0')
let revisionSerial = 0
const scope = (): WorkingDocumentSaveScope => ({ pj: 'project-A', documentId: s().documentId, format: s().documentFormat, loadSeq: s().loadSeq })
const puts = () => requests.filter(request => request.method === 'PUT' && request.path === `/api/autosave/${id}`)

beforeEach(async () => {
  vi.mocked(captureThumbSources).mockReturnValue(new Map())
  setCurrentProjectId(null); resetRestoreStateForTests()
  await s().switchDocument(emptyProject(), `receipt-reset-${++serial}`)
  projectFileEditor.clear(); localStorage.clear(); slots.clear(); requests.length = 0
  id = `receipt-${serial}`; selected = 'v4'; mode = 'ok'; writeFailure = false; headerFailure = false; gate = null; getStarted = undefined
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test'), path = url.pathname.replace('/api/documents/v4/', '/api/')
    const method = init?.method ?? 'GET', versioned = url.pathname.startsWith('/api/documents/v4/')
    requests.push({ path, method, url })
    const reply = (value: unknown, status = 200, revision?: string) => Response.json(value, {
      status, headers: { ...(versioned && !headerFailure ? { 'X-Tavotto-Document-Format': 'v4' } : {}), ...(revision ? { 'X-Tavotto-Revision': revision } : {}) },
    })
    const metadata = path.match(/^\/api\/layout-session\/project-file\/([^/]+)$/)
    if (metadata) {
      if (mode === 'unsupported') return reply({}, 404)
      if (mode === 'unavailable') return reply({ code: 'session_unavailable' }, 503)
      const slot = slots.get(metadata[1])
      return reply({ version: 1, status: 'ok', binding: null, intent_sequence: 0,
        edit: { known: true, axis: slot?.axis ?? null, autosave_revision: slot?.revision ?? null } })
    }
    const auto = path.match(/^\/api\/autosave\/([^/]+)$/)
    if (auto) {
      const slotId = auto[1], slot = slots.get(slotId)
      if (method === 'GET') {
        if (!slot) return reply({}, 404)
        const captured = structuredClone(slot)
        if (gate) { getStarted?.(); await gate }
        return reply(captured.document, 200, captured.revision)
      }
      if (method === 'PUT') {
        if (writeFailure) return reply({ code: 'disk_write_failed' }, 500)
        const document = JSON.parse(String(init?.body))
        const edit = JSON.parse(url.searchParams.get('project_file_edit') ?? 'null')
        const saved = { document, revision: hex(++revisionSerial), axis: edit?.axis ?? null }
        slots.set(slotId, saved)
        return reply({ ok: true, saved_at: 1, revision: saved.revision, ...(mode === 'unsupported' ? {} : {
          project_file: mode === 'metadataFailed' ? { state: 'unconfirmed' } : {
            state: 'accepted', version: 1, status: 'ok', binding: null, intent_sequence: 0,
            edit: { known: true, axis: saved.axis, autosave_revision: saved.revision },
          },
        }) })
      }
    }
    return reply({}, 404)
  }))
  setCurrentProjectId('project-A')
})
afterEach(() => { release?.(); release = undefined; setCurrentProjectId(null); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function install(empty = false) {
  const document = emptyProject(); document.schema = selected === 'v4' ? 4 : 3
  if (!empty) document.canvases[0].objects = [{ id: 'text', type: 'text', text: 'package content', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8 }]
  expect(await s().switchDocument(document, id, undefined, selected === 'v4' ? { format: selected } : undefined)).toBe(true)
  return scope()
}

describe('工作副本真实落盘回执', () => {
  it.each(['legacy', 'v4'] as const)('%s 非空安装只等原writer一次PUT，receipt来自实际AS正文/版本，不预留named票据', async format => {
    selected = format
    const installed = await install()
    const receipt = await waitForWorkingDocumentSave(installed)
    expect(puts()).toHaveLength(1)
    expect(receipt).toMatchObject({ address: { projectId: 'project-A', documentId: id, format }, loadSeq: installed.loadSeq, revision: slots.get(id)!.revision })
    expect(receipt.document).toEqual(slots.get(id)!.document)
    expect(receipt.capture.address).toEqual(receipt.address)
    expect(requests.some(request => request.path.endsWith('/reserve'))).toBe(false)
    expect(s().projectFile).toBeNull()
  })

  it('普通空saveNow仍零PUT且不能伪receipt；只有显式persistEmpty真写一次，两消费者不重复写', async () => {
    const installed = await install(true)
    expect(await saveNowWithResult()).toEqual({ state: 'clean', wrote: true })
    expect(puts()).toHaveLength(0)
    await expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
    const [a, b] = await Promise.all([waitForWorkingDocumentSave(installed, { persistEmpty: true }), waitForWorkingDocumentSave(installed, { persistEmpty: true })])
    expect(puts()).toHaveLength(1)
    expect(a.revision).toBe(b.revision)
    expect(a.document.schema).toBe(4)
    expect(a.document.canvases[0].objects).toEqual([])
    await waitForWorkingDocumentSave(installed, { persistEmpty: true })
    expect(puts()).toHaveLength(1)
  })

  it.each(['unavailable', 'unsupported', 'metadataFailed'] as const)('%s不会取得现代安装成功资格', async failure => {
    mode = failure
    const installed = await install()
    await expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
    expect(requests.some(request => request.path.endsWith('/reserve'))).toBe(false)
  })

  it('本次writer失败不认同址旧成功记录，也不补写/清本机稿', async () => {
    await waitForWorkingDocumentSave(await install())
    const oldRevision = slots.get(id)!.revision
    writeFailure = true
    const next = emptyProject(); next.schema = 4; next.canvases[0].guides = [{ axis: 'x', pos: 99 }]
    expect(await s().switchDocument(next, id, undefined, { format: 'v4' })).toBe(true)
    await expect(waitForWorkingDocumentSave(scope())).rejects.toThrow()
    expect(slots.get(id)!.revision).toBe(oldRevision)
    expect(s().saveState).toBe('save_error')
    expect(Object.keys(localStorage).some(key => key.includes('autosave.') && key.includes(id))).toBe(true)
  })

  it('同一安装后续PUT失败也不能把本次失败冒充更早的成功receipt', async () => {
    const installed = await install()
    await waitForWorkingDocumentSave(installed)
    writeFailure = true
    s().commit(literal('user edit'), draft => { draft.guides.push({ axis: 'x', pos: 42 }) })
    expect((await saveNowWithResult()).state).toBe('save_error')
    await expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
  })

  it('同id重新安装空稿且尚无新PUT，旧revision仍存在也不认旧loadSeq成功记录', async () => {
    await waitForWorkingDocumentSave(await install())
    const oldRevision = slots.get(id)!.revision
    suspendAutosaveFor(id)
    const empty = emptyProject(); empty.schema = 4
    expect(await s().switchDocument(empty, id, undefined, { format: 'v4' })).toBe(true)
    expect(puts()).toHaveLength(1)
    expect(slots.get(id)!.revision).toBe(oldRevision)
    await expect(waitForWorkingDocumentSave(scope())).rejects.toThrow()
  })

  it('成功后强制元信息重读503，保留的旧同revision view也不能继续授予receipt', async () => {
    const installed = await install()
    await waitForWorkingDocumentSave(installed)
    mode = 'unavailable'
    await hydrateProjectFile(undefined, true)
    await expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
    expect(puts()).toHaveLength(1)
  })

  it.each(['revision', 'format', 'header', 'missing'] as const)('AS实际%s漂移拒绝旧成功回执，零额外PUT', async change => {
    const installed = await install()
    await waitForWorkingDocumentSave(installed)
    if (change === 'revision') slots.get(id)!.revision = hex(9999)
    if (change === 'format') slots.get(id)!.document.schema = 3
    if (change === 'header') headerFailure = true
    if (change === 'missing') slots.delete(id)
    await expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
    expect(puts()).toHaveLength(1)
  })

  it.each(['project', 'loadSeq'] as const)('GET迟到时%s已变，旧receipt无权落到后来安装', async change => {
    const installed = await install()
    await waitForWorkingDocumentSave(installed)
    gate = new Promise(resolve => { release = resolve })
    const reached = new Promise<void>(resolve => { getStarted = resolve })
    const waiting = expect(waitForWorkingDocumentSave(installed)).rejects.toThrow()
    await reached
    if (change === 'project') setCurrentProjectId('project-B')
    else useDocumentStore.setState({ loadSeq: s().loadSeq + 1 })
    release!(); gate = null
    await waiting
    expect(puts()).toHaveLength(1)
  })

  it('moment沿原只读快照保留PNG Promise，不因深拷贝变成保存失败', async () => {
    let finish!: (frame: EnginePngSnapshot) => void
    const frame = new Promise<EnginePngSnapshot>(resolve => { finish = resolve })
    const previewPngSnapshot = vi.fn(() => frame)
    const restore = setEngineTransport({ supportsChartRender: true, supportsChartSourceRender: true,
      render: async () => { throw new Error('This source fixture never renders SVG.') },
      previewPngUrl: async () => '', previewPngSnapshot, panelSrc: () => null })
    const result = { url: 'data:image/png;base64,fixture', manifest: { elements: [] } as unknown as Manifest, chartRevision: 'paired' }
    try {
      const document = emptyProject(); document.schema = 4
      const vector = chartVectors.vectors.find(value => value.expect === 'ok')!
      const panel: PanelObject = { id: 'owned', type: 'panel', fileId: 'original.svg', fileKind: 'svg', nativeW: 100, nativeH: 80,
        x: 0, y: 0, w: 100, h: 80, overrides: [],
        chart: structuredClone({ model: vector.model, binding: vector.binding }) as unknown as PanelObject['chart'],
        chartSource: { schema: 1, sourceId: 'original.svg', bytesSha256: 'a'.repeat(64), sizeBytes: 5, sceneDigest: vector.model!.scene_digest! } }
      document.canvases[0].objects = [panel]
      expect(await s().switchDocument(document, id, undefined, { format: 'v4' })).toBe(true)
      const installed = scope(), waiting = waitForWorkingDocumentSave(installed)
      await expect(waiting).resolves.toMatchObject({ revision: expect.any(String) })
      const receipt = await waiting, png = receipt.moment?.thumb?.get('owned')?.png
      expect(png).toBeInstanceOf(Promise)
      expect((await waitForWorkingDocumentSave(installed)).moment?.thumb?.get('owned')?.png).toBe(png)
      expect(previewPngSnapshot).toHaveBeenCalledTimes(1)
      finish(result)
      await expect(png).resolves.toBe(result.url)
    } finally { finish(result); restore() }
  })
})
