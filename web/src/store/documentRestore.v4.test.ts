import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'
import { setCurrentProjectId } from '@/lib/session'
import { armRestoreToken, beginRestore, useDocumentStore } from './documentStore'
import { captureClues, openAlternative, resetRestoreStateForTests, resolveProjectDocument, setRestoreRetryDelays } from './documentRestore'
import '@/store/projectStore'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const bodies: Record<string, ProjectDocument> = {}
let ledger: Record<string, unknown>
let requestNumber = 0
let snapshotAt: ((n: number) => string) | null = null
let sourceFailure = false
let readGate: Promise<void> | null = null
let readEntered = false
const writes: { path: string; body: Record<string, unknown> }[] = []
const text = (value: string): TextObject => ({ id: 't', type: 'text', text: value, x: 0, y: 0, w: 30, h: 10, sizePt: 9, bold: false, color: '#000', align: 'left' })
const pd = (schema: 3 | 4, label: string) => { const p = emptyProject(); p.schema = schema; p.canvases[0].objects = [text(label)]; return p }
beforeEach(async () => {
  localStorage.clear(); setCurrentProjectId('p_intent'); writes.length = 0; requestNumber = 0; snapshotAt = null; sourceFailure = false; readGate = null; readEntered = false
  bodies.legacy = pd(3, 'old'); bodies.v4 = pd(4, 'new')
  ledger = { version: 1, status: 'ok', sequence: 2, snapshot: 'same', latest: { document_format: 'v4', doc_id: 'same', pointer: 'last' } }
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = new URL(String(url), 'http://localhost'), path = u.pathname, format = path.startsWith('/api/documents/v4/') ? 'v4' : 'legacy'
    const headers: Record<string, string> = format === 'v4' ? { 'X-Tavotto-Document-Format': 'v4' } : {}
    if (init?.method === 'PUT') { writes.push({ path, body: JSON.parse(String(init.body)) }); return Response.json({ ok: true, state: 'durable', last: { doc_id: 'same', name: format, at: 10001 } }) }
    if (path.endsWith('/owner')) return Response.json({ owner: 'this', evidence: 'owners', verified: true, revision: 'r' }, { headers })
    if (path.endsWith('/layout-session')) {
      requestNumber++
      const intent = { ...ledger, ...(snapshotAt ? { snapshot: snapshotAt(requestNumber) } : {}) }
      const pending = format === 'v4' && (ledger.latest as { pointer?: string } | null)?.pointer === 'pending'
      return Response.json({ last: pending ? null : { doc_id: 'same', name: format, at: format === 'legacy' ? 10000 : 500, revision: 'r' }, prev: null, pending: pending ? { doc_id: 'same', name: format, at: 500, revision: null } : null, index: 'ok', ...(u.searchParams.has('include_open_intent') ? { open_intent: intent } : {}) }, { headers })
    }
    if (path.includes('/autosave/')) {
      if (format === 'legacy' && readGate) { readEntered = true; await readGate }
      if (sourceFailure && format === 'v4') return new Response('{}', { status: 500 })
      return Response.json(bodies[format], { headers: { ...headers, 'X-Tavotto-Revision': 'r' } })
    }
    return new Response('{}', { status: 404 })
  }))
  resetRestoreStateForTests(); setRestoreRetryDelays([0, 0])
  await useDocumentStore.getState().switchDocument(emptyProject(), 'placeholder', undefined, { placeholder: true })
  writes.length = 0
})
afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })
const resolve = async () => { const clues = captureClues('p_intent'), token = beginRestore('p_intent'); armRestoreToken(token); return resolveProjectDocument(token, clues) }

describe('双namespace冷恢复使用共享意图，无本机cache', () => {
  it('同id两格式且v4 at因回拨小于legacy，也按共享latest恢复v4；自动安装不分新intent', async () => {
    const result = await resolve()
    expect(result.kind).toBe('restored'); expect(useDocumentStore.getState().documentFormat).toBe('v4')
    expect((useDocumentStore.getState().doc.objects[0] as TextObject).text).toBe('new')
    expect(writes.filter((w) => w.path.endsWith('/last') && w.body.repair !== true)).toEqual([])
  })
  it('自动采用已落正文的pending也不经安装订阅分新intent', async () => {
    (ledger.latest as { pointer: string }).pointer = 'pending'
    expect((await resolve()).kind).toBe('restored')
    expect(writes.filter(w => w.path.endsWith('/last') && w.body.repair !== true)).toEqual([])
  })
  it('两GET快照不同先有界重读，稳定之后才恢复', async () => {
    snapshotAt = n => n === 1 ? 'before' : 'after'
    expect((await resolve()).kind).toBe('restored'); expect(requestNumber).toBeGreaterThanOrEqual(4)
  })
  it('快照持续变化不能按at猜，终态needs_user且零正文写入', async () => {
    snapshotAt = n => String(n)
    const result = await resolve()
    expect(result).toMatchObject({ kind: 'needs_user', issue: { kind: 'index_degraded' } })
    expect(writes).toEqual([]); expect(requestNumber).toBe(6)
  })
  it('选中v4正文读失败，不能借可读legacy旧稿冒充恢复成功', async () => {
    sourceFailure = true
    expect(await resolve()).toMatchObject({ kind: 'needs_user', issue: { kind: 'unavailable', ref: { id: 'same', format: 'v4' } } })
    expect(useDocumentStore.getState().documentId).toBe('placeholder')
  })
  it('两namespace未跟踪先needs_user；用户显式选择v4同id后只对该格式分一次新intent', async () => {
    ledger = { version: 1, status: 'untracked', snapshot: 'same' }
    const result = await resolve()
    expect(result).toMatchObject({ kind: 'needs_user', issue: { kind: 'choose' } })
    expect(useDocumentStore.getState().restoreIssue?.alternatives.map(a => [a.format ?? 'legacy', a.id])).toEqual([['legacy', 'same'], ['v4', 'same']])
    expect((await openAlternative('same', 'v4')).kind).toBe('restored')
    expect(useDocumentStore.getState().documentFormat).toBe('v4')
    await vi.waitFor(() => expect(writes.filter(w => w.path.endsWith('/last') && w.body.repair !== true)).toHaveLength(1))
    expect(writes.at(-1)?.path).toBe('/api/documents/v4/layout-session/last')
  })
  it('同id但未被验证提供的另一格式不能借已验证legacy候选获得安装权', async () => {
    ledger = { version: 1, status: 'untracked', snapshot: 'same' }; sourceFailure = true
    expect((await resolve()).kind).toBe('needs_user')
    expect(useDocumentStore.getState().restoreIssue?.alternatives.map(a => a.format ?? 'legacy')).toEqual(['legacy'])
    sourceFailure = false
    expect(await openAlternative('same', 'v4')).toEqual({ kind: 'superseded' })
    expect(useDocumentStore.getState().documentId).toBe('placeholder')
  })

  const oldChartProject = () => {
    const vector = chartVectors.vectors.find(v => v.expect === 'ok')!
    bodies.legacy.canvases[0].objects = [{ id: 'p', type: 'panel', fileId: 'old.svg', fileKind: 'svg', x: 0, y: 0, w: 100, h: 80, nativeW: 100, nativeH: 80, overrides: [], chart: structuredClone({ model: vector.model, binding: vector.binding }) } as never]
    ledger.latest = { document_format: 'legacy', doc_id: 'same', pointer: 'last' }
  }
  it('旧schema3含chart冷恢复成功fork新v4身份时记录一次升级intent，旧正文不动', async () => {
    oldChartProject(); const original = JSON.stringify(bodies.legacy)
    expect((await resolve()).kind).toBe('restored')
    const s = useDocumentStore.getState()
    expect(s.documentFormat).toBe('v4'); expect(s.documentId).not.toBe('same')
    await vi.waitFor(() => expect(writes.filter(w => w.path.endsWith('/last') && w.body.repair !== true && w.body.doc_id === s.documentId)).toHaveLength(1))
    expect(writes.find(w => w.body.doc_id === s.documentId)?.path).toBe('/api/documents/v4/layout-session/last')
    expect(JSON.stringify(bodies.legacy)).toBe(original)
  })
  it('旧schema3 chart probe迟到且已被显式换文档取代，不fork也不记录旧升级intent', async () => {
    oldChartProject()
    let release!: () => void; readGate = new Promise<void>(r => { release = r })
    const pending = resolve(); await vi.waitFor(() => expect(readEntered).toBe(true))
    await useDocumentStore.getState().switchDocument(emptyProject(), 'newer-explicit')
    release(); expect(await pending).toEqual({ kind: 'superseded' })
    expect(useDocumentStore.getState().documentId).toBe('newer-explicit')
    expect(useDocumentStore.getState().documentFormat).toBe('legacy')
    expect(writes.filter(w => w.path.startsWith('/api/documents/v4/') && w.path.endsWith('/last'))).toEqual([])
  })

})
