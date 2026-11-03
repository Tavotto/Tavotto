import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { emptyProject, type PanelObject, type FigureSnapshot, type FigureExecutionOwner } from '@/types/document'
import type { FigureSnapshotInvalidation } from '@/lib/api'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()), probeScript: vi.fn(),
}))
const be = createFakeBackend()
const owner = { script: 'plot.py', run_config: 'A' }
const fact = (id: string, over: Partial<FigureSnapshotInvalidation> = {}): FigureSnapshotInvalidation => ({
  scope: 'owner', ...owner, source_ids: [], execution_id: id, ...over,
})
const ref = (sourceId: string, executionOwner?: FigureExecutionOwner): FigureSnapshot => ({
  version: 1, sourceId, patchHash: 'patch', receiptId: `old:${sourceId}:${executionOwner?.run_config}`,
  snapshotId: `snapshot:${sourceId}`, ...(executionOwner ? { executionOwner } : {}),
})
const panel = (id: string, config = 'A', script = 'plot.py'): PanelObject => ({
  id, type: 'panel', fileId: `runtime:${id}`, fileKind: 'runtime', script, figureFrame: 1,
  nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides: [],
  source: { script, runConfig: config, entry: '__main__', stem: id, captureSource: 'pyplot', fingerprint: 'old', sizeMm: [80, 60] },
  figureSnapshot: ref(`runtime:${id}`),
})
const document = () => {
  const pd = emptyProject()
  pd.canvases[0].objects = [panel('old-A'), panel('other-config', 'B'), panel('other-script', 'A', 'other.py')]
  const inactive = structuredClone(pd.canvases[0]); inactive.id = 'inactive'
  inactive.objects = [panel('omitted-B')]
  const a = { ...panel('shared-A'), fileId: 'shared.pdf', fileKind: 'pdf' as const, source: undefined, figureSnapshot: ref('shared.pdf', owner) }
  const b = { ...a, id: 'shared-B', figureSnapshot: ref('shared.pdf', { ...owner, run_config: 'B' }) }
  inactive.objects.push(a, b, { ...a, id: 'unverified-static', figureSnapshot: ref('shared.pdf') })
  pd.canvases.push(inactive)
  return pd
}
let stop: (() => void) | undefined
let close: (() => void) | undefined
beforeEach(() => {
  vi.resetModules(); localStorage.clear(); sessionStorage.clear(); be.reset(); vi.stubGlobal('fetch', be.fetch)
})
afterEach(() => { stop?.(); close?.(); stop = close = undefined; vi.unstubAllGlobals() })
async function boot(placeholder = false) {
  const session = await import('@/lib/session'); session.setCurrentProjectId('A')
  const doc = await import('./documentStore')
  const runs = await import('./scriptRunStore')
  const api = await import('@/lib/api')
  await doc.useDocumentStore.getState().switchDocument(placeholder ? emptyProject() : document(), 'layout', undefined, placeholder ? { placeholder: true } : undefined)
  if (!placeholder) {
    const token = doc.beginRestore('A'); doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await doc.saveNow(); be.state.calls.length = 0
  }
  stop = doc.startAutosave()
  close = () => { runs.useScriptRunStore.getState().clear(); session.setCurrentProjectId(null) }
  return { session, doc, runs, api }
}
const refs = (app: Awaited<ReturnType<typeof boot>>) => Object.fromEntries(app.doc.useDocumentStore.getState().buildProject().canvases.flatMap(c => c.objects).map(o => [o.id, (o as PanelObject).figureSnapshot]))
async function assertRetired(app: Awaited<ReturnType<typeof boot>>) {
  const result = refs(app)
  expect(result['old-A']).toBeUndefined(); expect(result['omitted-B']).toBeUndefined(); expect(result['shared-A']).toBeUndefined()
  expect(result['other-config']).toBeDefined(); expect(result['other-script']).toBeDefined()
  expect(result['shared-B']).toBeDefined(); expect(result['unverified-static']).toBeDefined()
  await vi.waitFor(() => {
    expect(be.writes()).toContain('PUT autosave layout')
    expect(JSON.parse(be.state.slots.get('layout')!.body).canvases[1].objects[0].figureSnapshot).toBeUndefined()
  }, { timeout: 2200 })
  const cold = await app.doc.readAutosaveDoc('layout')
  expect((cold.doc!.canvases[1].objects[0] as PanelObject).figureSnapshot).toBeUndefined()
}

describe('trusted execution output-set lifecycle', () => {
  it.each(['only-A', 'renamed', 'zero', 'partial-failure', 'whole-failure'] as const)('%s retires the entire exact owner and actually persists it', async (outcome) => {
    const app = await boot()
    const error = outcome.includes('failure') ? { code: 'script_probe_failed', message: 'failed after execution' } : undefined
    const descriptors = outcome === 'zero' || outcome === 'whole-failure' ? [] : [{ asset_id: outcome === 'renamed' ? 'runtime:renamed' : 'runtime:old-A' }]
    vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors, error, figure_snapshot_invalidation: fact(outcome) } as never)
    await app.runs.useScriptRunStore.getState().run('plot.py', 'A')
    await assertRetired(app)
  })

  it.each(['script_no_figure', 'dependency_preparation_required', 'execution_cancelled'])('without actual-start evidence %s preserves refs', async (code) => {
    const app = await boot()
    vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors: [], error: { code, message: 'not executed' } } as never)
    await app.runs.useScriptRunStore.getState().run('plot.py', 'A')
    expect(refs(app)['omitted-B']).toBeDefined()
    expect(be.writes()).toEqual([])
  })

  it('live SSE retires before a long run returns; HTTP replay and later receipt delta do not clear a new run ref', async () => {
    const app = await boot()
    const { handleServerEvent } = await import('@/hooks/useServerEvents')
    let finish!: (value: never) => void
    vi.mocked(app.api.probeScript).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const run = app.runs.useScriptRunStore.getState().run('plot.py', 'A')
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'other-project', ...fact('live') })
    expect(refs(app)['old-A']).toBeDefined()
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...fact('live') })
    await assertRetired(app)
    const newRef = { ...ref('runtime:old-A', owner), receiptId: 'new-current' }
    app.doc.mergeSavedFigureSnapshots(app.doc.captureFigureSnapshotSave(app.doc.useDocumentStore.getState().buildProject()), { 'old-A': newRef })
    finish({ descriptors: [], figure_snapshot_invalidation: fact('live') } as never)
    await run
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...fact('live', { receipt_ids: ['old:shared.pdf:A'] }) })
    expect(refs(app)['old-A']).toEqual(newRef)
    const writes = be.writes().length
    await new Promise(resolve => setTimeout(resolve, 1100))
    expect(be.writes()).toHaveLength(writes)
  })

  it('unknown-config native source scope retires only its explicit output, never a guessed default owner', async () => {
    const app = await boot()
    const { handleServerEvent } = await import('@/hooks/useServerEvents')
    app.doc.useDocumentStore.getState().commit(literal('default config'), d => {
      const p = panel('default'); p.source!.runConfig = undefined; d.objects.push(p)
    })
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...fact('native-unknown', {
      scope: 'sources', run_config: null, source_ids: ['runtime:old-A'],
    }) })
    expect(refs(app)['old-A']).toBeUndefined()
    for (const id of ['default', 'omitted-B', 'shared-A', 'shared-B']) expect(refs(app)[id]).toBeDefined()
    await vi.waitFor(() => {
      expect(be.writes()).toContain('PUT autosave layout')
      expect(JSON.parse(be.state.slots.get('layout')!.body).canvases[0].objects[0].figureSnapshot).toBeUndefined()
    }, { timeout: 2200 })
  })

  it('pending owner survives A/B/A restoration, sees omitted runtime outputs only after loading, then is consumed', async () => {
    const app = await boot(true)
    app.doc.beginRestore('A')
    vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors: [], figure_snapshot_invalidation: fact('pending-zero') } as never)
    await app.runs.useScriptRunStore.getState().run('plot.py', 'A')
    app.session.setCurrentProjectId('B')
    let token = app.doc.beginRestore('B')
    app.doc.installRestoredDocument(token, document(), 'other', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await Promise.resolve(); expect(refs(app)['omitted-B']).toBeDefined()
    app.session.setCurrentProjectId('A')
    token = app.doc.beginRestore('A')
    app.doc.installRestoredDocument(token, document(), 'layout', null)
    app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
    await Promise.resolve(); await assertRetired(app)
    await app.doc.useDocumentStore.getState().switchDocument(document(), 'historical')
    expect(refs(app)['omitted-B']).toBeDefined()
  })

  it.each(['ready', 'pending-restore'] as const)('%s: late archive deltas stay bound to the consumed installation, not a later historical open', async (mode) => {
    const app = await boot(mode === 'pending-restore')
    const { handleServerEvent } = await import('@/hooks/useServerEvents')
    const execution = fact('late-history-delta')
    if (mode === 'pending-restore') app.doc.beginRestore('A')
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...execution })
    if (mode === 'pending-restore') {
      const token = app.doc.beginRestore('A')
      app.doc.installRestoredDocument(token, document(), 'layout', null)
      app.doc.settleRestore(token, { state: 'ready', reason: 'restored' })
      await Promise.resolve()
    }
    await assertRetired(app)
    // The first delta still belongs to this installation and must retire the ownerless old archive.
    const delta = { ...execution, receipt_ids: ['old:shared.pdf:undefined'] }
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...delta })
    expect(refs(app)['unverified-static']).toBeUndefined()
    await app.doc.saveNow()
    const historical = document()
    const old = historical.canvases[1].objects[3] as PanelObject
    old.figureSnapshot = { ...old.figureSnapshot!, receiptId: 'archive-discovered-later' }
    await app.doc.useDocumentStore.getState().switchDocument(historical, 'explicit-old-history')
    await app.doc.saveNow()
    const autosaves = () => be.writes().filter(write => write.startsWith('PUT autosave'))
    const before = autosaves().length
    handleServerEvent({ kind: 'figure.snapshot_invalidated', pj: 'A', ...execution, receipt_ids: ['archive-discovered-later'] })
    expect(refs(app)['unverified-static']?.receiptId).toBe('archive-discovered-later')
    await new Promise(resolve => setTimeout(resolve, 1100))
    expect(autosaves()).toHaveLength(before)
  })

  it('owner retirement covers undo-only outputs without changing unrelated history geometry', async () => {
    const app = await boot()
    const store = app.doc.useDocumentStore
    store.getState().commit(literal('remove'), d => { d.objects = d.objects.filter(o => o.id !== 'old-A') })
    vi.mocked(app.api.probeScript).mockResolvedValueOnce({ descriptors: [], figure_snapshot_invalidation: fact('undo-zero') } as never)
    await app.runs.useScriptRunStore.getState().run('plot.py', 'A')
    store.getState().undo()
    expect(refs(app)['old-A']).toBeUndefined()
    expect((store.getState().doc.objects.find(o => o.id === 'old-A') as PanelObject).x).toBe(0)
    await assertRetired(app)
  })
})
