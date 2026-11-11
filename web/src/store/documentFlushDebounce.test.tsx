import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TextView } from '@/canvas/TextView'
import { literal } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { documentNamespaceKey } from '@/lib/documentFormat'
import { projectFileEditor } from '@/lib/projectFileSession'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'
import { resetRestoreStateForTests } from './documentRestore'
import { flushAutosave, saveNowWithResult, startAutosave, useDocumentStore, waitForWorkingDocumentSave } from './documentStore'
// Install the real synchronous Moment source port, including its empty text-only map.
import './renderStore'

const state = () => useDocumentStore.getState()
const deferred = () => {
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  return { promise, resolve }
}
const settle = async () => { for (let i = 0; i < 24; i++) await Promise.resolve() }
type Request = { method: string; path: string; body?: ProjectDocument }
type Slot = { document: ProjectDocument; revision: string; axis: unknown }
let serial = 0
let id: string
let requests: Request[]
let slots: Map<string, Slot>
let metadata: ReturnType<typeof deferred>
let metadataStarted: ReturnType<typeof deferred>
let putStarted: ReturnType<typeof deferred>
let putGate: ReturnType<typeof deferred> | null
let failure: 409 | 500 | 'network' | null
let stop: (() => void) | undefined
let host: HTMLDivElement | undefined
let root: Root | undefined
const puts = () => requests.filter(request => request.method === 'PUT' && request.path === `/api/autosave/${id}`)
const localKey = () => documentNamespaceKey(`${PRODUCT_NAME.toLowerCase()}.autosave.${id}`, 'v4')
const scope = () => ({ pj: 'project-A', documentId: id, format: 'v4' as const, loadSeq: state().loadSeq })
const edit = (text: string) => state().commit(literal('edit'), doc => { (doc.objects[0] as TextObject).text = text })
const textOf = (document: ProjectDocument) => (document.canvases[0].objects[0] as TextObject).text

beforeEach(async () => {
  setCurrentProjectId(null); resetRestoreStateForTests()
  await state().switchDocument(emptyProject(), `debounce-reset-${++serial}`)
  localStorage.clear(); projectFileEditor.clear()
  id = `debounce-${serial}`; requests = []; slots = new Map()
  metadata = deferred(); metadataStarted = deferred(); putStarted = deferred(); putGate = null; failure = null
  vi.useFakeTimers(); vi.setSystemTime(100000)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {}; disconnect() {} })
  // The browser's 22px text line is 5.820833mm. Exercise TextView's actual layout effect.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(22)
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test'), path = url.pathname.replace('/api/documents/v4/', '/api/')
    const method = init?.method ?? 'GET', versioned = url.pathname.startsWith('/api/documents/v4/')
    const body = method === 'PUT' ? JSON.parse(String(init?.body)) as ProjectDocument : undefined
    requests.push({ method, path, ...(body ? { body } : {}) })
    const reply = (value: unknown, status = 200, revision?: string) => Response.json(value, {
      status, headers: { ...(versioned ? { 'X-Tavotto-Document-Format': 'v4' } : {}), ...(revision ? { 'X-Tavotto-Revision': revision } : {}) },
    })
    if (path === `/api/layout-session/project-file/${id}`) {
      metadataStarted.resolve(); await metadata.promise
      const slot = slots.get(id)
      return reply({ version: 1, status: 'ok', binding: null, intent_sequence: 0,
        edit: { known: true, axis: slot?.axis ?? null, autosave_revision: slot?.revision ?? null } })
    }
    if (path === `/api/autosave/${id}`) {
      const slot = slots.get(id)
      if (method === 'GET') return slot ? reply(slot.document, 200, slot.revision) : reply({}, 404)
      if (method === 'PUT') {
        putStarted.resolve(); if (putGate) await putGate.promise
        if (failure === 'network') throw new TypeError('Failed to fetch')
        if (failure) return reply({ code: failure === 409 ? 'stale_write' : 'write_failed', error: 'write rejected' }, failure)
        const edit = JSON.parse(url.searchParams.get('project_file_edit') ?? 'null')
        const saved = { document: body!, revision: puts().length.toString(16).padStart(32, '0'), axis: edit?.axis ?? null }
        slots.set(id, saved)
        return reply({ ok: true, saved_at: Date.now(), revision: saved.revision, project_file: {
          state: 'accepted', version: 1, status: 'ok', binding: null, intent_sequence: 0,
          edit: { known: true, axis: saved.axis, autosave_revision: saved.revision },
        } })
      }
    }
    return reply({}, 404)
  }))
  setCurrentProjectId('project-A')
  stop = startAutosave()
})

afterEach(async () => {
  metadata.resolve(); putGate?.resolve(); stop?.(); stop = undefined
  await settle()
  if (root) act(() => root!.unmount())
  root = undefined; host?.remove(); host = undefined
  setCurrentProjectId(null); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

async function beginInstall() {
  const document = emptyProject(); document.schema = 4
  document.canvases[0].objects = [{ id: 'text', type: 'text', text: 'installed text', sizePt: 9,
    bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 90, h: 10 }]
  const installed = state().switchDocument(document, id, undefined, { format: 'v4' })
  await metadataStarted.promise
  host = window.document.createElement('div'); window.document.body.append(host); root = createRoot(host)
  act(() => root!.render(<TextView obj={state().doc.objects[0] as TextObject} />))
  expect(state().doc.objects[0].h).toBeCloseTo(5.820833333333333)
  return { installed }
}

async function finishInstall(installed: Promise<boolean>) {
  metadata.resolve(); expect(await installed).toBe(true)
  return waitForWorkingDocumentSave(scope())
}

describe('立即保存消费同一次捕获已覆盖的防抖', () => {
  it('leaving flush writes the last edit while project-file metadata is still pending', async () => {
    const { installed } = await beginInstall()
    edit('last edit before leaving')
    // A document/project switch must not lose the last edit behind hydration.
    expect(flushAutosave({ leaving: true })).toBe('saved')
    await settle()
    const leaves = puts().filter(request => request.body && textOf(request.body) === 'last edit before leaving')
    expect(leaves).toHaveLength(1)
    metadata.resolve()
    expect(await installed).toBe(true)
    await settle()
  })

  it('真实TextView挂载测量已入首次正文与Moment；安装加防抖只发一次PUT', async () => {
    const { installed } = await beginInstall()
    const receipt = await finishInstall(installed)
    await vi.advanceTimersByTimeAsync(2500)
    expect(puts()).toHaveLength(1)
    expect(receipt.document).toEqual(slots.get(id)!.document)
    expect(receipt.document.canvases[0].objects[0].h).toBeCloseTo(5.820833333333333)
    expect(receipt.moment!.identity.doc.objects[0].h).toBeCloseTo(5.820833333333333)
    expect(receipt.capture.address).toEqual(receipt.address)
  })

  it('metadata慢过防抖且期间再编辑：原deferred只捕获最新正文一次，不重复安装flush', async () => {
    const { installed } = await beginInstall()
    await vi.advanceTimersByTimeAsync(1000)
    expect(puts()).toHaveLength(0)
    act(() => edit('edited while metadata pending'))
    await vi.advanceTimersByTimeAsync(500)
    const receipt = await finishInstall(installed)
    await vi.advanceTimersByTimeAsync(2000)
    expect(puts()).toHaveLength(1)
    expect(textOf(receipt.document)).toBe('edited while metadata pending')
    expect((receipt.moment!.identity.doc.objects[0] as TextObject).text).toBe('edited while metadata pending')
    expect(requests.filter(row => row.path === `/api/layout-session/project-file/${id}`)).toHaveLength(1)
  })

  it.each([false, true])('首次PUT阻塞中后续编辑的timer独立保留（先回执=%s）', async releaseBeforeTimer => {
    putGate = deferred()
    const { installed } = await beginInstall()
    metadata.resolve(); expect(await installed).toBe(true); await putStarted.promise
    const first = puts()[0].body!
    await vi.advanceTimersByTimeAsync(200)
    act(() => edit('real edit after first capture'))
    if (releaseBeforeTimer) { putGate.resolve(); await settle() }
    await vi.advanceTimersByTimeAsync(999)
    expect(puts()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    putGate.resolve()
    const receipt = await waitForWorkingDocumentSave(scope())
    expect(puts()).toHaveLength(2)
    expect(textOf(first)).toBe('installed text')
    expect(textOf(receipt.document)).toBe('real edit after first capture')
    expect((receipt.moment!.identity.doc.objects[0] as TextObject).text).toBe('real edit after first capture')
    await vi.advanceTimersByTimeAsync(2500)
    expect(puts()).toHaveLength(2)
  })

  it('一次flush后新一轮编辑仍独立防抖并捕获新Moment', async () => {
    const { installed } = await beginInstall()
    const first = await finishInstall(installed)
    act(() => edit('later independent edit'))
    await vi.advanceTimersByTimeAsync(999)
    expect(puts()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    const later = await waitForWorkingDocumentSave(scope())
    expect(puts()).toHaveLength(2)
    expect(textOf(first.document)).toBe('installed text')
    expect((first.moment!.identity.doc.objects[0] as TextObject).text).toBe('installed text')
    expect(textOf(later.document)).toBe('later independent edit')
    expect(later.moment).not.toBe(first.moment)
  })

  it.each([500, 'network'] as const)('%s失败不由旧timer偷偷重试，原稿和显式重试保留', async rejection => {
    failure = rejection
    const { installed } = await beginInstall()
    await expect(finishInstall(installed)).rejects.toThrow()
    expect(state().saveState).toBe('save_error')
    await vi.advanceTimersByTimeAsync(2500)
    expect(puts()).toHaveLength(1)
    expect(slots.has(id)).toBe(false)
    expect(localStorage.getItem(localKey())).not.toBeNull()
    failure = null
    expect(await saveNowWithResult()).toMatchObject({ wrote: true })
    expect(puts()).toHaveLength(2)
    expect(textOf((await waitForWorkingDocumentSave(scope())).document)).toBe('installed text')
  })

  it('409仍保冲突及本机新稿；后续编辑和flush不得越过原裁决门', async () => {
    failure = 409
    const { installed } = await beginInstall()
    await expect(finishInstall(installed)).rejects.toThrow()
    expect(state().saveState).toBe('conflict')
    failure = null
    act(() => edit('edit after conflict'))
    flushAutosave()
    await vi.advanceTimersByTimeAsync(2500)
    expect(puts()).toHaveLength(1)
    expect(state().saveState).toBe('conflict')
    const local = JSON.parse(localStorage.getItem(localKey())!) as ProjectDocument
    expect(textOf(local)).toBe('edit after conflict')
    await expect(waitForWorkingDocumentSave(scope())).rejects.toThrow()
  })

  it('无pj安装仍经原立即flush保存，无metadata或Moment授权旁路', async () => {
    setCurrentProjectId(null)
    const document = emptyProject(); document.schema = 4
    document.canvases[0].guides.push({ axis: 'x', pos: 12 })
    expect(await state().switchDocument(document, id, undefined, { format: 'v4' })).toBe(true)
    await settle(); await vi.advanceTimersByTimeAsync(2500)
    expect(puts()).toHaveLength(1)
    expect(slots.get(id)!.document.canvases[0].guides).toEqual([{ axis: 'x', pos: 12 }])
    expect(requests.some(row => row.path.includes('layout-session'))).toBe(false)
  })
})
