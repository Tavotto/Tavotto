import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type FigureSnapshot, type PanelObject, type ProjectDocument } from '@/types/document'
import type { FigureSnapshotInvalidation, PreparationReport } from '@/lib/api'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  createPreparationSession: vi.fn(), actOnPreparationSession: vi.fn(), probeScript: vi.fn(),
}))

const be = createFakeBackend()
const snapshot = (sourceId: string): FigureSnapshot => ({
  version: 1, sourceId, patchHash: 'patch', receiptId: 'old-run', snapshotId: `snapshot:${sourceId}`,
})
const panel = (id: string, fileId: string, script: string): PanelObject => ({
  id, type: 'panel', fileId, fileKind: 'raster', script, figureFrame: 1,
  nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides: [], figureSnapshot: snapshot(fileId),
})
const savedDocument = (): ProjectDocument => {
  const pd = emptyProject()
  pd.canvases[0].objects = [panel('p1', 'Fig1.png', 'plot.py'), panel('p2', 'Other.png', 'other.py')]
  return pd
}
const execution = (id = 'execution-one', sources = ['Fig1.png', 'Fig1.pdf']): FigureSnapshotInvalidation => ({
  scope: 'owner', script: 'plot.py', run_config: null, source_ids: sources, execution_id: id,
})
const report = (completed = false): PreparationReport => ({
  ...(completed ? { figure_snapshot_invalidation: execution() } : {}),
  session_version: 1, session_id: 'session-one', project_id: 'pj-a',
  target: { kind: 'script', script: 'plot.py', entry: '__main__', asset_id: null, stem: null },
  config_revision: 1, observation_seq: completed ? 2 : 1,
  phase: completed ? 'completed' : 'ready_to_run',
  outcome: { kind: completed ? 'succeeded' : 'pending' },
  facts: { execution_finished: completed ? true : null, figure_captured: completed ? true : null },
  checks: [], requirements: [], runtime_input: null, result: null,
  actions: [{ id: 'run-one', kind: 'run', config_revision: 1,
    impact: { executes_user_script: true, installs_packages: false, changes_environment: false, writes_to_project: [] } }],
  provider: { plan_id: 'plan-one', attempt_id: completed ? 'attempt-one' : null, attempts: completed ? 1 : 0, dependency: null },
  // Only the restored panel reveals the alternate .png source. Placeholder-time mapping cannot find it.
  captured: completed ? [{ asset_id: 'runtime:Fig1', script: 'plot.py', stem: 'Fig1', original_artifact: 'Fig1.pdf' } as never] : [],
})
let stopAutosave: (() => void) | undefined
let cleanup: (() => void) | undefined
beforeEach(() => {
  vi.resetModules()
  localStorage.clear(); sessionStorage.clear(); be.reset()
  vi.stubGlobal('fetch', be.fetch)
})
afterEach(() => {
  stopAutosave?.(); stopAutosave = undefined
  cleanup?.(); cleanup = undefined
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
async function boot() {
  const session = await import('@/lib/session')
  session.setCurrentProjectId('pj-a')
  const doc = await import('./documentStore')
  const prep = await import('./projectPreparationStore')
  const api = await import('@/lib/api')
  const runs = await import('./scriptRunStore')
  prep.__setPreparationTimingForTests({ pollMs: [1_000_000] })
  cleanup = () => { prep.useProjectPreparationStore.getState().clear(); runs.useScriptRunStore.getState().clear(); session.setCurrentProjectId(null) }
  await doc.useDocumentStore.getState().switchDocument(emptyProject(), 'placeholder', undefined, { placeholder: true })
  stopAutosave = doc.startAutosave()
  return { session, doc, prep, api, runs }
}
async function completeRun(app: Awaited<ReturnType<typeof boot>>, via: 'preparation' | 'script' = 'preparation') {
  const completed = { ...report(true), figure_snapshot_invalidation: execution(`run-${Math.random()}`) }
  if (via === 'script') {
    vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors: completed.captured!, figure_snapshot_invalidation: completed.figure_snapshot_invalidation } as never)
    await app.runs.useScriptRunStore.getState().run('plot.py', null)
    return
  }
  vi.mocked(app.api.createPreparationSession).mockResolvedValueOnce(report())
  await app.prep.useProjectPreparationStore.getState().open(app.prep.scriptTarget('plot.py'))
  vi.mocked(app.api.actOnPreparationSession).mockResolvedValueOnce({ claimed: true, report: completed })
  await app.prep.useProjectPreparationStore.getState().act('script:plot.py', 'run')
}

describe('successful executions survive document restoration', () => {
  it.each([
    ['restoring', 'preparation'], ['needs_user', 'preparation'],
    ['restoring', 'script'], ['needs_user', 'script'],
  ] as const)('%s: %s success retires the restored source and automatically writes it to disk', async (mode, via) => {
    const app = await boot()
    const { doc } = app
    be.putSlot('saved-layout', savedDocument(), 'pj-a')
    let token = doc.beginRestore('pj-a')
    doc.armRestoreToken(token)
    if (mode === 'needs_user') doc.settleRestore(token, { state: 'needs_user', issue: {
      pj: 'pj-a', gen: token.gen, kind: 'unavailable', ref: { id: 'saved-layout', name: 'Saved' }, alternatives: [],
    } })
    await completeRun(app, via)
    expect(doc.useDocumentStore.getState().doc.objects).toEqual([])
    expect(be.writes()).toEqual([])
    if (mode === 'needs_user') { token = doc.beginRestore('pj-a'); doc.armRestoreToken(token) }
    const restored = await doc.readAutosaveDoc('saved-layout')
    expect(doc.installRestoredDocument(token, restored.doc!, 'saved-layout', null)).toBe(true)
    // Installing under the token is not ready yet; no placeholder/half-installed document may be written.
    expect(be.writes()).toEqual([])
    expect(doc.settleRestore(token, { state: 'ready', reason: 'restored' })).toBe(true)
    await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave saved-layout'), { timeout: 2200 })
    const persisted = JSON.parse(be.state.slots.get('saved-layout')!.body) as ProjectDocument
    expect((persisted.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    expect((persisted.canvases[0].objects[1] as PanelObject).figureSnapshot).toEqual(snapshot('Other.png'))
    const reopened = await doc.readAutosaveDoc('saved-layout')
    expect((reopened.doc!.canvases[0].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
  })


  it('keeps an execution accepted in initial-ready until the first real restored document', async () => {
    const app = await boot()
    expect(app.doc.getRestoreGate().reason).toBe('initial')
    await completeRun(app)
    be.putSlot('initial-layout', savedDocument(), 'pj-a')
    const token = app.doc.beginRestore('pj-a')
    app.doc.armRestoreToken(token)
    const restored = await app.doc.readAutosaveDoc('initial-layout')
    app.doc.installRestoredDocument(token, restored.doc!, 'initial-layout', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave initial-layout'), { timeout: 2200 })
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toBeUndefined()
  })

  it('needs_user longer than the readiness timeout still consumes on retry, exactly once', async () => {
    const app = await boot()
    const token = app.doc.beginRestore('pj-a')
    app.doc.settleRestore(token, { state: 'needs_user', issue: {
      pj: 'pj-a', gen: token.gen, kind: 'unavailable', ref: null, alternatives: [],
    } })
    await completeRun(app)
    vi.useFakeTimers()
    await vi.advanceTimersByTimeAsync(app.doc.DOCUMENT_READY_TIMEOUT_MS + 1)
    const retry = app.doc.beginRestore('pj-a')
    app.doc.armRestoreToken(retry)
    app.doc.installRestoredDocument(retry, savedDocument(), 'long-restore', null)
    app.doc.settleRestore(retry, { state: 'ready', reason: 'restored' })
    await vi.advanceTimersByTimeAsync(1100)
    expect(be.writes()).toEqual(['PUT autosave long-restore'])
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toBeUndefined()
    await vi.advanceTimersByTimeAsync(2200)
    expect(be.writes()).toEqual(['PUT autosave long-restore'])
  })

  it('retains accepted A facts through B, consumes on A restoration, then preserves a later historical open', async () => {
    const app = await boot()
    app.doc.beginRestore('pj-a')
    await completeRun(app)
    app.session.setCurrentProjectId('pj-b')
    let token = app.doc.beginRestore('pj-b')
    app.doc.installRestoredDocument(token, savedDocument(), 'b-layout', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await Promise.resolve()
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toEqual(snapshot('Fig1.png'))
    expect(be.writes()).toEqual([])
    app.session.setCurrentProjectId('pj-a')
    token = app.doc.beginRestore('pj-a')
    app.doc.installRestoredDocument(token, savedDocument(), 'a-layout', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave a-layout'), { timeout: 2200 })
    expect(be.writes()).not.toContain('PUT autosave b-layout')
    await app.doc.useDocumentStore.getState().switchDocument(savedDocument(), 'historical-a')
    await Promise.resolve()
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toEqual(snapshot('Fig1.png'))
    expect(app.doc.useDocumentStore.getState().saveState).toBe('clean')
    // A new explicit run still affects that historical document even though gate.docId names the
    // earlier restoration. Only replay of the already-consumed old run was suppressed above.
    await completeRun(app, 'script')
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toBeUndefined()
  })

  it.each(['project-round-trip', 'project-close', 'new-attempt'] as const)('does not admit a late unaccepted preparation success after %s', async (change) => {
    const app = await boot()
    app.doc.beginRestore('pj-a')
    vi.mocked(app.api.createPreparationSession).mockResolvedValueOnce(report())
    await app.prep.useProjectPreparationStore.getState().open(app.prep.scriptTarget('plot.py'))
    let resolve!: (value: Awaited<ReturnType<typeof app.api.actOnPreparationSession>>) => void
    vi.mocked(app.api.actOnPreparationSession).mockImplementationOnce(() => new Promise((r) => { resolve = r }))
    const running = app.prep.useProjectPreparationStore.getState().act('script:plot.py', 'run')
    if (change === 'new-attempt') {
      vi.mocked(app.api.createPreparationSession).mockResolvedValueOnce({ ...report(), session_id: 'new-session' })
      await app.prep.useProjectPreparationStore.getState().open({ script: 'plot.py', argv: ['--new'] })
    } else {
      app.session.setCurrentProjectId(change === 'project-close' ? null : 'pj-b')
      app.session.setCurrentProjectId('pj-a')
    }
    resolve({ claimed: true, report: report(true) })
    await running
    const token = app.doc.beginRestore('pj-a')
    app.doc.installRestoredDocument(token, savedDocument(), 'no-late-success', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await Promise.resolve()
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toEqual(snapshot('Fig1.png'))
    expect(be.writes()).toEqual([])
  })

  it.each([false, true])('retains every exact artifact when a configured capture changes PNG to PDF (legacy Windows ID=%s)', async (windowsId) => {
    const app = await boot()
    const token = app.doc.beginRestore('pj-a')
    const pd = emptyProject()
    pd.canvases[0].objects = [
      panel('png', windowsId ? String.raw`out\Fig1.png` : 'out/Fig1.png', 'plot.py'),
      { ...panel('pdf', 'out/Fig1.pdf', 'plot.py'), fileKind: 'pdf' },
      panel('same-stem', 'out/Fig1.svg', 'plot.py'),
      panel('other', 'Other.png', 'other.py'),
    ]
    be.putSlot('two-artifacts', pd, 'pj-a')
    for (const artifact of [String.raw`out\Fig1.png`, 'out/Fig1.pdf']) {
      const descriptor = { ...report(true).captured![0],
        asset_id: 'runtime:Fig1@rc1', run_config: 'rc1', stem: 'out/Fig1', original_artifact: artifact,
      }
      vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors: [descriptor], figure_snapshot_invalidation: { ...execution(artifact, [artifact]), run_config: 'rc1' } } as never)
      await app.runs.useScriptRunStore.getState().run('plot.py', 'rc1')
    }
    const restored = await app.doc.readAutosaveDoc('two-artifacts')
    app.doc.installRestoredDocument(token, restored.doc!, 'two-artifacts', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave two-artifacts'), { timeout: 2200 })
    const persisted = JSON.parse(be.state.slots.get('two-artifacts')!.body) as ProjectDocument
    const panels = persisted.canvases[0].objects as PanelObject[]
    expect(panels[0].figureSnapshot).toBeUndefined()
    expect(panels[1].figureSnapshot).toBeUndefined()
    // run_config still forbids broad same-stem invalidation; only the two observed exact artifacts retire.
    expect(panels[2].figureSnapshot).toEqual(snapshot('out/Fig1.svg'))
    expect(panels[3].figureSnapshot).toEqual(snapshot('Other.png'))
  })

  it('waits until switchDocument finishes installing after it first publishes ready', async () => {
    const app = await boot()
    app.doc.beginRestore('pj-a')
    await completeRun(app)
    await app.doc.useDocumentStore.getState().switchDocument(savedDocument(), 'explicit-restored')
    await vi.waitFor(() => expect(be.writes()).toContain('PUT autosave explicit-restored'), { timeout: 2200 })
    expect((app.doc.useDocumentStore.getState().doc.objects[0] as PanelObject).figureSnapshot).toBeUndefined()
  })
})
