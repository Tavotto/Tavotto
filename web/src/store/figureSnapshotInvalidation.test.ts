import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentProjectId } from '@/lib/session'
import { invalidateFigureSnapshots, useDocumentStore } from './documentStore'
import { invalidateFigureExecution } from './figureSnapshotInvalidation'
import type { PanelObject } from '@/types/document'
import { useRuntimeAssetStore } from './runtimeAssetStore'

vi.mock('./documentStore', () => ({
  invalidateFigureSnapshots: vi.fn(),
  getRestoreGate: () => ({ state: 'ready', reason: 'restored', pj: 'project-a', docId: 'doc' }),
  useDocumentStore: { getState: vi.fn() },
}))

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('project-a')
  useRuntimeAssetStore.getState().clear()
})

describe('trusted execution invalidation covers the whole open document', () => {
  it('an execution from another project does not read or modify the current document', () => {
    invalidateFigureExecution({ scope: 'owner', script: 'plot.py', run_config: null,
      source_ids: [], execution_id: 'foreign' }, 'project-b')
    expect(useDocumentStore.getState).not.toHaveBeenCalled()
    expect(invalidateFigureSnapshots).not.toHaveBeenCalled()
  })

  it('same-token receipt deltas add exact retirements without replaying the owner', () => {
    vi.mocked(useDocumentStore.getState).mockReturnValue({
      documentId: 'doc', buildProject: () => ({ canvases: [{ objects: [] }] }),
    } as never)
    const fact = { scope: 'owner' as const, script: 'plot.py', run_config: 'A', source_ids: [], execution_id: 'delta' }
    invalidateFigureExecution(fact, 'project-a')
    invalidateFigureExecution({ ...fact, receipt_ids: ['retired-A'] }, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenLastCalledWith([], 'project-a', ['retired-A'])
    invalidateFigureExecution({ ...fact, receipt_ids: ['retired-A'] }, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledTimes(2)
  })

  it('retires the exact owner across inactive canvases/history, including omitted B; repeats are inert', () => {
    const runtime = (fileId: string, runConfig = 'rc-one') => ({
      type: 'panel', fileKind: 'runtime', fileId, overrides: [],
      source: { script: 'plot.py', runConfig },
    } as unknown as PanelObject)
    vi.mocked(useDocumentStore.getState).mockReturnValue({
      documentId: 'doc',
      buildProject: () => ({ canvases: [
        { objects: [runtime('runtime:A'), runtime('runtime:other-config', 'rc-two')] },
        { objects: [runtime('runtime:B')] },
      ] }),
      past: [{ inverse: [{ value: runtime('runtime:removed-C') }] }],
    } as never)
    const execution = { scope: 'owner' as const, script: 'plot.py', run_config: 'rc-one', source_ids: [], execution_id: 'unit-owner-run-1' }
    invalidateFigureExecution(execution, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledExactlyOnceWith(['runtime:A', 'runtime:B', 'runtime:removed-C'], 'project-a', [], [{ script: 'plot.py', run_config: 'rc-one' }])
    invalidateFigureExecution(execution, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledTimes(1)
    invalidateFigureExecution({ ...execution, execution_id: 'unit-owner-run-2' }, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledTimes(2)
  })

  it('unknown-config native sources scope cannot retire the default owner', () => {
    vi.mocked(useDocumentStore.getState).mockReturnValue({
      documentId: 'doc', buildProject: () => ({ canvases: [{ objects: [
        { type: 'panel', fileKind: 'runtime', fileId: 'runtime:default', source: { script: 'plot.py' } },
        { type: 'panel', fileKind: 'pdf', fileId: String.raw`out\B.pdf`, script: 'plot.py' },
      ] }] }),
    } as never)
    invalidateFigureExecution({ scope: 'sources', script: 'plot.py', run_config: null,
      source_ids: ['runtime:native-old-B', 'out/B.pdf'], execution_id: 'unit-sources-run' }, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledExactlyOnceWith(
      ['runtime:native-old-B', 'out/B.pdf', String.raw`out\B.pdf`], 'project-a',
    )
  })
})
