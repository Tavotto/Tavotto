import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { CapturedFigureDescriptor, ProbeResult } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useFigurePickerStore } from './figurePickerStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useScriptRunStore } from './scriptRunStore'
import { useUiStore } from './uiStore'
import { useWorkspaceStore } from './workspace'
import { runPngRebuild } from './pngAdmissionMemory'
import { useEnvStore } from './envStore'
import { GATE_CANCEL_GRACE_MS, rebuildEditableFromScript } from './pngRebuild'

const probe = vi.fn()
vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  probeScript: (...a: unknown[]) => probe(...a),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  engineRender: vi.fn().mockRejectedValue(new Error('no render in this test')),
}))
vi.mock('./figureSnapshotInvalidation', () => ({}))

const desc = (stem: string, script = 'fig.py'): CapturedFigureDescriptor => ({
  asset_id: `runtime:${script}#${stem}`, script, entry: '__main__', stem, capture_source: 'savefig', execution_profile: 'safe',
  original_artifact: null, size_mm: [50, 40], source_fingerprint: 'sha256:x', can_writeback_artifact: false, can_writeback_source: false,
})
const ok = (descriptors: CapturedFigureDescriptor[]): ProbeResult => ({
  script: 'fig.py', entry: '__main__', stems: descriptors.map((d) => d.stem), descriptors, error: null, tried: ['__main__'], registered: true,
})
const pngPanel = (): PanelObject => ({
  id: 'p', type: 'panel', fileId: 'out/same.png', fileKind: 'raster', script: 'fig.py', figureFrame: 1,
  nativeW: 50, nativeH: 40, pxW: 400, pxH: 320, x: 10, y: 12, w: 50, h: 40, overrides: [],
})
const S = () => useDocumentStore.getState()
const panels = () => S().doc.objects.filter((o): o is PanelObject => o.type === 'panel')

beforeEach(async () => {
  probe.mockReset()
  setCurrentProjectId('A')
  useScriptRunStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  useFigurePickerStore.getState().close()
  useWorkspaceStore.getState().clear()
  useUiStore.getState().setStatus(null)
  useEnvStore.setState({ dependencyPreparation: null, workdirConfirmation: null })
  await S().switchDocument(emptyProject(), 'rebuild-test')
  S().commit(literal('seed'), (d) => { d.objects.push(pngPanel()) })
  useDocumentStore.setState({ past: [], future: [] })
})

describe('rebuild as editable figure (ADR 0139 PR1)', () => {
  it('adds a new runtime panel beside the PNG and leaves the PNG panel exactly as it was', async () => {
    probe.mockResolvedValue(ok([desc('same')]))
    const before = structuredClone(panels()[0])
    expect(await rebuildEditableFromScript('p')).toBe('rebuilt')
    const all = panels()
    expect(all).toHaveLength(2)
    expect(all[0]).toEqual(before) // overrides / guard / position / pixels source: untouched
    expect(all[0].artifactValidation).toBeUndefined()
    const added = all[1]
    expect(added.fileKind).toBe('runtime')
    expect(added.fileId).toBe('runtime:fig.py#same')
    expect(added.overrides).toEqual([])
    expect(added.artifactValidation).toBeUndefined()
    const overlap = !(added.x >= before.x + before.w || before.x >= added.x + added.w || added.y >= before.y + before.h || before.y >= added.y + added.h)
    expect(overlap).toBe(false)
    expect(useUiStore.getState().status).toEqual({ key: 'artifact.rebuilt', ns: 'errors' })
    expect(useUiStore.getState().statusTone).toBe('done')
    expect(probe).toHaveBeenCalledTimes(1)
  })
  it('never defaults to the first figure: no unique script+stem match hands over to the Figure picker', async () => {
    probe.mockResolvedValue(ok([desc('other'), desc('another')]))
    expect(await rebuildEditableFromScript('p')).toBe('picker')
    expect(useFigurePickerStore.getState().script).toBe('fig.py')
    expect(panels()).toHaveLength(1)
  })
  it('picks the matching figure out of several, not the first one', async () => {
    probe.mockResolvedValue(ok([desc('other'), desc('same')]))
    expect(await rebuildEditableFromScript('p')).toBe('rebuilt')
    expect(panels().map((p) => p.fileId)).toEqual(['out/same.png', 'runtime:fig.py#same'])
  })
  it('a failed script run says so once and leaves the PNG untouched', async () => {
    probe.mockResolvedValue({ ...ok([]), error: { code: 'script_error', message: 'boom', params: {} } })
    const before = structuredClone(panels()[0])
    expect(await rebuildEditableFromScript('p')).toBe('failed')
    expect(panels()).toEqual([before])
    expect(useUiStore.getState().status).toEqual({ key: 'artifact.rebuildFailed', ns: 'errors' })
  })
  it('the toast button reaches this implementation through the registered runner', async () => {
    probe.mockResolvedValue(ok([desc('same')]))
    runPngRebuild('p')
    await vi.waitFor(() => expect(panels()).toHaveLength(2))
    expect(probe).toHaveBeenCalledTimes(1)
  })
})

describe('rebuild: setup gates and the originating layout (Codex #1204)', () => {
  const offer = {
    code: 'dependency_preparation_required', script: 'fig.py',
    plan: { status: 'ready', script: 'fig.py', requirements: ['adjusttext'] },
    target_kind: 'tavotto_managed', targets: [], rounds_remaining: 3, skipped: false,
  }
  const confirmation = {
    kind: 'workdir', code: 'workdir_confirmation_required', script: 'fig.py', reason: 'script_dir_evidence',
    recommended: 'project', options: [], conflicts: [], reads: [],
  }
  const gated = (code: string, extra: Record<string, unknown>): ProbeResult => ({
    ...ok([]), error: { code, message: 'gate', ...extra } as ProbeResult['error'],
  })
  const row = () => useScriptRunStore.getState().byScript['fig.py']
  afterEach(() => { vi.useRealTimers() })

  const approve = (phase: 'needs_workdir' | 'needs_preparation') => {
    useEnvStore.setState({ dependencyPreparation: null, workdirConfirmation: null })
    useScriptRunStore.getState().rerunGated(phase, 'fig.py')
  }

  it.each([
    ['needs_workdir', gated('workdir_confirmation_required', { confirmation })],
    ['needs_preparation', gated('dependency_preparation_required', { dependency_preparation: offer })],
  ] as const)('%s: after the user approves and the re-run lands, the editable panel appears with one extra run', async (phase, gate) => {
    probe.mockResolvedValueOnce(gate)
    const done = rebuildEditableFromScript('p')
    await vi.waitFor(() => expect(row()?.phase).toBe(phase))
    expect(panels()).toHaveLength(1)
    probe.mockResolvedValueOnce(ok([desc('same')]))
    approve(phase)
    expect(await done).toBe('rebuilt')
    expect(panels().map((p) => p.fileId)).toEqual(['out/same.png', 'runtime:fig.py#same'])
    expect(probe).toHaveBeenCalledTimes(2) // 第一次撞门 + 门后唯一一次重跑
    expect(useUiStore.getState().status).toEqual({ key: 'artifact.rebuilt', ns: 'errors' })
  })

  it('a gate the user cancels ends quietly: no panel, no error toast', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    probe.mockResolvedValueOnce(gated('workdir_confirmation_required', { confirmation }))
    const done = rebuildEditableFromScript('p')
    await vi.waitFor(() => expect(row()?.phase).toBe('needs_workdir'))
    useEnvStore.getState().dismissWorkdirConfirmation()
    await vi.advanceTimersByTimeAsync(GATE_CANCEL_GRACE_MS + 10)
    expect(await done).toBe('failed')
    expect(panels()).toHaveLength(1)
    expect(useUiStore.getState().status).toBeNull()
    expect(probe).toHaveBeenCalledTimes(1)
  })

  it('opening another saved layout mid-run adds nothing to either layout and leaves the PNG panel alone', async () => {
    let release!: (r: ProbeResult) => void
    probe.mockReturnValueOnce(new Promise<ProbeResult>((r) => { release = r }))
    const before = structuredClone(panels()[0])
    const done = rebuildEditableFromScript('p')
    await vi.waitFor(() => expect(probe).toHaveBeenCalled())
    // 另一份已保存的排版里恰好有同 id 的面板：只看「面板还在」会放行
    await S().switchDocument(emptyProject(), 'other-layout')
    S().commit(literal('seed'), (d) => { d.objects.push(pngPanel()) })
    const other = structuredClone(panels())
    release(ok([desc('same')]))
    expect(await done).toBe('stale')
    expect(panels()).toEqual(other)
    expect(other).toHaveLength(1)
    expect(before.fileKind).toBe('raster')
    expect(useUiStore.getState().status).toBeNull()
  })
})
