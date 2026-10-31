import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FigureSnapshotInvalidation, NativePending, NativeSessionInfo } from '@/lib/api'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type PanelObject } from '@/types/document'

const be = createFakeBackend()
const execution: FigureSnapshotInvalidation = {
  scope: 'sources', script: 'plot.py', run_config: null, source_ids: [],
  receipt_ids: ['old-receipt'], execution_id: 'native-after-continue',
}
const session = (over: Partial<NativeSessionInfo> = {}): NativeSessionInfo => ({
  session_id: 'native-http', project_root: '/project', interpreter: '/python',
  interpreter_fingerprint: 'python', target_kind: 'script', target_display: 'plot.py',
  cwd: '/project', arg_count: 1, python_version: '3.12', state: 'barrier',
  barrier_reason: 'show', process_pid: 1, stems: [], descriptors: [],
  script_error: null, terminal_error: null, exit_code: null, figures_captured: 1,
  started_at: 1, last_event_at: 1, sequence: 4, editable: true, ...over,
})
const savedDocument = () => {
  const project = emptyProject()
  project.canvases[0].objects = [{
    id: 'panel', type: 'panel', fileId: 'figure.pdf', fileKind: 'pdf',
    nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides: [],
    figureSnapshot: { version: 1, sourceId: 'figure.pdf', patchHash: 'patch', receiptId: 'old-receipt', snapshotId: 'snapshot' },
  } satisfies PanelObject]
  return project
}
let stop: (() => void) | undefined
let cleanup: (() => void) | undefined
beforeEach(() => { vi.resetModules(); localStorage.clear(); sessionStorage.clear(); be.reset() })
afterEach(() => { stop?.(); cleanup?.(); stop = cleanup = undefined; vi.unstubAllGlobals() })

async function boot(response: NativeSessionInfo, error = false, approvalPj?: string | null) {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url), 'http://localhost').pathname
    if (path.startsWith('/api/native/')) {
      requests.push(`${init?.method ?? 'GET'} ${path}`)
      if (path === '/api/native/sessions') return new Response(JSON.stringify({ sessions: [response] }))
      return new Response(JSON.stringify(error
        ? { code: 'native_session_disconnected', figure_snapshot_invalidation: response.figure_snapshot_invalidation }
        : { session: response, pj: approvalPj }), { status: error ? 500 : 200 })
    }
    return be.fetch(url as RequestInfo, init)
  }))
  const project = await import('@/lib/session'); project.setCurrentProjectId('project')
  const doc = await import('./documentStore')
  const native = await import('./nativeSessionStore')
  await doc.useDocumentStore.getState().switchDocument(savedDocument(), 'layout')
  doc.settleRestore(doc.beginRestore('project'), { state: 'ready', reason: 'restored' })
  await doc.saveNow(); be.state.calls.length = 0
  native.useNativeSessionStore.setState({ sessions: { 'native-http': session() } })
  const input = await import('./scriptInputStore')
  native.syncNativePendingInput(session({ pending_input: { request_id: 'question', kind: 'input', prompt: 'Continue?', masked: false, ordinal: 1, at: 1 } }))
  stop = doc.startAutosave()
  cleanup = () => { native.useNativeSessionStore.getState().clear(); project.setCurrentProjectId(null) }
  return { doc, native: native.useNativeSessionStore, input: input.useScriptInputStore, requests, project, response }
}

type Action = 'resume' | 'detach' | 'terminate' | 'submit' | 'stop'
const act = (app: Awaited<ReturnType<typeof boot>>, action: Action) => action === 'submit'
  ? app.input.getState().submit(null) : action === 'stop'
    ? app.input.getState().stop() : app.native.getState()[action]('native-http')
const pending: NativePending = {
  native_id: 'pending', created_at: 1, expires_at: 999, project_root: '/project', interpreter: '/python',
  cwd: '/project', target_kind: 'script', target_display: 'plot.py', arg_count: 1,
  command_fingerprint: 'command', permission_key: 'permission', python_version: '3.12', remembered: false,
}

describe('native HTTP execution facts without SSE', () => {
  describe.each(['resume', 'detach', 'terminate', 'submit', 'stop'] as const)('%s', (action) => {
    it.each(['success', 'error'] as const)('%s persists receipt-only retirement through the real autosave queue and cold read', async (outcome) => {
      const terminal = session({ state: 'ended', sequence: 5, editable: false, figure_snapshot_invalidation: execution })
      const app = await boot(terminal, outcome === 'error')
      // No EventSource or applyEvent: the action HTTP response is the only execution evidence.
      await act(app, action)
      if (outcome === 'success') {
        if (action !== 'submit' && action !== 'stop') expect(app.native.getState().sessions['native-http']).toEqual(terminal)
      } else if (action === 'submit' || action === 'stop') expect(app.input.getState().error).toBeTruthy()
      else expect(app.native.getState().errors['native-http']?.code).toBe('native_session_disconnected')
      await vi.waitFor(() => {
        expect(be.writes()).toContain('PUT autosave layout')
        expect(JSON.parse(be.state.slots.get('layout')!.body).canvases[0].objects[0].figureSnapshot).toBeUndefined()
      }, { timeout: 2200 })
      const cold = await app.doc.readAutosaveDoc('layout')
      expect((cold.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
      // Only reconcile after proving the HTTP response itself reached durable storage.
      if (outcome === 'success') await app.native.getState().refresh('/project')
      const endpoint = { resume: 'continue', detach: 'detach', terminate: 'terminate', submit: 'input', stop: 'cancel' }[action]
      expect(app.requests).toContain(`POST /api/native/sessions/native-http/${endpoint}`)
      expect(be.writes().filter(write => write.startsWith('PUT autosave'))).toHaveLength(1)
    })

    it.each(['cancelled', 'failed', 'pre-start-error'] as const)('%s without a fact preserves saved references and makes no autosave write', async (outcome) => {
      const app = await boot(session({ state: outcome === 'failed' ? 'failed' : 'ended', sequence: 5, cancelled: outcome === 'cancelled' }), outcome === 'pre-start-error')
      await act(app, action)
      await new Promise(resolve => setTimeout(resolve, 1100))
      expect(be.writes()).toEqual([])
      const cold = await app.doc.readAutosaveDoc('layout')
      expect((cold.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot?.receiptId).toBe('old-receipt')
    })
  })

  it.each(['project', 'other-project', undefined] as const)('approval consumes only its backend-verified project: %s', async (pj) => {
    const app = await boot(session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: execution }), false, pj)
    app.native.setState({ sessions: {}, pendingQueue: [{ native_id: 'pending', info: pending, loading: false, submitting: false, error: null }] })
    await app.native.getState().approve('pending', false)
    if (pj === 'project') {
      await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave layout'), { timeout: 2200 })
    } else {
      // A foreign/authority-less approved card cannot later authorize this project's actions/input.
      app.response.sequence += 1
      app.response.figure_snapshot_invalidation = { ...execution, execution_id: 'new-foreign-execution' }
      await app.native.getState().refresh()
      await act(app, 'resume')
      await act(app, 'submit')
      await new Promise(resolve => setTimeout(resolve, 1100))
      expect(be.writes()).toEqual([])
    }
    const cold = await app.doc.readAutosaveDoc('layout')
    expect((cold.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot?.receiptId).toBe(pj === 'project' ? undefined : 'old-receipt')
  })

  it('an unfiltered first listing cannot make a foreign action or input retire this project', async () => {
    const app = await boot(session({ project_root: '/foreign', state: 'running_script', sequence: 5 }))
    app.native.setState({ sessions: {} })
    await app.native.getState().refresh()
    app.response.sequence = 6
    app.response.figure_snapshot_invalidation = execution
    await act(app, 'resume')
    await act(app, 'submit')
    await new Promise(resolve => setTimeout(resolve, 1100))
    expect(be.writes()).toEqual([])
    const cold = await app.doc.readAutosaveDoc('layout')
    expect((cold.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot?.receiptId).toBe('old-receipt')
  })

  it.each(['same-token', 'new-token', 'already-observed-delta'] as const)('late approval preserves the execution boundary: %s', async (mode) => {
    const app = await boot(session({ state: 'running_script', sequence: 5, figure_snapshot_invalidation: execution }), false, 'project')
    app.native.setState({ sessions: {}, pendingQueue: [{ native_id: 'pending', info: pending, loading: false, submitting: false, error: null }] })
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    const fetch = globalThis.fetch
    vi.stubGlobal('fetch', async (url: unknown, init?: RequestInit) => {
      if (new URL(String(url), 'http://localhost').pathname.endsWith('/approve')) await held
      return fetch(url as RequestInfo, init)
    })
    const approval = app.native.getState().approve('pending', false)
    if (mode === 'already-observed-delta') {
      const { handleServerEvent } = await import('@/hooks/useServerEvents')
      handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'project', ...execution, receipt_ids: [] })
    }
    app.project.setCurrentProjectId('other-project'); app.project.setCurrentProjectId('project')
    if (mode !== 'already-observed-delta') {
      await app.doc.useDocumentStore.getState().switchDocument(savedDocument(), 'historical')
      await app.doc.saveNow(); be.state.calls.length = 0
    }
    release(); await approval
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toBeDefined()
    app.response.sequence = 6
    app.response.state = 'ended'
    if (mode === 'new-token') app.response.figure_snapshot_invalidation = { ...execution, execution_id: 'actual-next-execution' }
    await act(app, 'terminate')
    const id = mode === 'already-observed-delta' ? 'layout' : 'historical'
    if (mode === 'same-token') {
      await new Promise(resolve => setTimeout(resolve, 1100))
      expect(be.writes()).toEqual([])
    } else await vi.waitFor(() => expect(be.writes()).toContain(`PUT autosave ${id}`), { timeout: 2200 })
    const cold = await app.doc.readAutosaveDoc(id)
    expect((cold.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot?.receiptId).toBe(mode === 'same-token' ? 'old-receipt' : undefined)
  })
})
