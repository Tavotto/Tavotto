import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { documentNamespaceKey } from './documentFormat'
import { readProjectDocument, rememberProjectDocument, planRestore } from './projectDocs'
import { knownLayoutRevision, rememberLayoutName, rememberLayoutRevision } from './layoutRevision'
import { readProjectFile, writeProjectFile } from './projectFile'
import { setCurrentProjectId } from './session'

const requests: string[] = []
beforeEach(() => {
  localStorage.clear(); requests.length = 0; setCurrentProjectId('pj')
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url), 'http://localhost').pathname; requests.push(path)
    const v4 = path.startsWith('/api/documents/v4/')
    const last = { doc_id: v4 ? 'd4' : 'd3', name: v4 ? 'V4' : 'Legacy', at: 1, revision: 'r' }
    const body = init?.method === 'PUT' ? { ok: true, state: 'durable', last } : { last, prev: [], pending: null, index: 'ok' }
    return Response.json(body, { headers: v4 ? { 'X-Tavotto-Document-Format': 'v4' } : {} })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCurrentProjectId(null) })

describe('同一算法的两格式状态隔离', () => {
  it('同id项目文件绑定与修订号不会串，旧键和旧名称保留', () => {
    const old = { projectId: 'pj', name: 'Old', file: 'tavottofile/Old.json', revision: 'r3', dirty: false }
    const newer = { ...old, file: 'tavottofile/documents/v4/Old.json', revision: 'r4' }
    writeProjectFile('same', old)
    const storedOld = readProjectFile('same')
    const oldBytes = localStorage.getItem('tavotto.projectFile.same')
    expect(storedOld).toMatchObject(old)
    writeProjectFile('same', newer, 'v4')
    expect(readProjectFile('same')).toEqual(storedOld)
    expect(readProjectFile('same', 'v4')).toMatchObject(newer)
    expect(localStorage.getItem('tavotto.projectFile.same')).toBe(oldBytes)
    rememberLayoutName('raw same', 'canonical3'); rememberLayoutName('raw same', 'canonical4', 'v4')
    rememberLayoutRevision('canonical3', 'r3', 'pj'); rememberLayoutRevision('canonical4', 'r4', 'pj', 'v4')
    expect(knownLayoutRevision('raw same', 'pj')).toBe('r3')
    expect(knownLayoutRevision('raw same', 'pj', 'v4')).toBe('r4')
  })
  it('显式remember分别到各namespace，本地缓存不会跨格式去重', async () => {
    rememberProjectDocument('pj', { id: 'same', name: 'Legacy' })
    rememberProjectDocument('pj', { id: 'same', name: 'Chart', format: 'v4' })
    await vi.waitFor(() => expect(requests.filter(p => p.endsWith('/last'))).toHaveLength(2))
    expect(readProjectDocument('pj')).toEqual({ id: 'same', name: 'Legacy' })
    expect(readProjectDocument('pj', 'v4')).toEqual({ id: 'same', name: 'Chart', format: 'v4' })
    expect(localStorage.getItem(documentNamespaceKey('tavotto.projectDoc.pj', 'v4'))).not.toBeNull()
  })
  it('两namespace分别复用原计划规则，不用跨namespace at推断顺序', async () => {
    const legacy = await planRestore('pj', { globalCurrent: null })
    const chart = await planRestore('pj', { globalCurrent: null }, 'v4')
    expect(legacy.preferred?.id).toBe('d3'); expect(legacy).not.toHaveProperty('format')
    expect(chart.preferred).toMatchObject({ id: 'd4', format: 'v4' }); expect(chart.format).toBe('v4')
  })
})
import { notifyDocumentPersisted } from './projectDocs'

it.each(['superseded', 'unconfirmed'] as const)('迟到v4保存补推只repair，%s不假ack或清待确认', async (state) => {
  const sent: Record<string, unknown>[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>; sent.push(body)
    return Response.json(body.repair ? { ok: true, state, last: null } : { ok: true, state: 'pending', last: null })
  }))
  rememberProjectDocument('p_repair', { id: 'd4', name: 'Chart', format: 'v4' })
  await vi.waitFor(() => expect(sent).toHaveLength(1))
  const key = documentNamespaceKey('tavotto.projectDoc.p_repair', 'v4')
  const before = localStorage.getItem(key)
  notifyDocumentPersisted('d4', 'p_repair', 'v4')
  await vi.waitFor(() => expect(sent).toHaveLength(2))
  expect(sent[0]).toEqual({ doc_id: 'd4', name: 'Chart' })
  expect(sent[1]).toEqual({ doc_id: 'd4', name: 'Chart', repair: true })
  expect(localStorage.getItem(key)).toBe(before)
  expect(JSON.parse(before!).pendingGen).toEqual(expect.any(String))
})
it('跨namespace显式打开共用发送队列：legacy A慢请求完成前不能发送后开的v4 B，最终仍是B', async () => {
  let release!: () => void
  const gate = new Promise<void>(r => { release = r })
  const sent: string[] = []
  let latest = ''
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url), 'http://localhost').pathname
    const format = path.startsWith('/api/documents/v4/') ? 'v4' : 'legacy'
    const body = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
    sent.push(format)
    if (format === 'legacy') await gate
    latest = body.doc_id
    return Response.json({ ok: true, state: 'durable', last: { ...body, at: 1 } })
  }))
  rememberProjectDocument('p_order', { id: 'A', name: 'Legacy' })
  await vi.waitFor(() => expect(sent).toEqual(['legacy']))
  rememberProjectDocument('p_order', { id: 'B', name: 'Chart', format: 'v4' })
  try {
    for (let i = 0; i < 8; i++) await Promise.resolve()
    expect(sent).toEqual(['legacy'])
  } finally { release() }
  await vi.waitFor(() => expect(latest).toBe('B'))
  expect(sent).toEqual(['legacy', 'v4'])
})
