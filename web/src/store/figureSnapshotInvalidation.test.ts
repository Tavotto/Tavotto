import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CapturedFigureDescriptor } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { invalidateFigureSnapshots, useDocumentStore } from './documentStore'
import { invalidateCapturedFigureSnapshots } from './figureSnapshotInvalidation'

vi.mock('./documentStore', () => ({
  invalidateFigureSnapshots: vi.fn(),
  useDocumentStore: { getState: vi.fn() },
}))

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('project-a')
})

describe('successful run invalidation covers the whole open document', () => {
  it('maps sources from inactive canvases as well as the currently displayed one', () => {
    vi.mocked(useDocumentStore.getState).mockReturnValue({
      buildProject: () => ({ canvases: [
        { objects: [{ type: 'panel', fileKind: 'pdf', fileId: 'Fig1.pdf', script: 'plot.py' }] },
        { objects: [{ type: 'panel', fileKind: 'pdf', fileId: 'Fig2.pdf', script: 'plot.py' }] },
      ] }),
    } as never)
    const descriptors = ['Fig1', 'Fig2'].map((stem) => ({
      asset_id: `runtime:${stem}`, stem, script: 'plot.py', original_artifact: null,
    })) as CapturedFigureDescriptor[]
    invalidateCapturedFigureSnapshots(descriptors, 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledWith(
      ['runtime:Fig1', 'runtime:Fig2', 'Fig1.pdf', 'Fig2.pdf'], 'project-a',
    )
  })

  it('a completed run from another project does not even read the current document', () => {
    invalidateCapturedFigureSnapshots([], 'project-b')
    expect(useDocumentStore.getState).not.toHaveBeenCalled()
    expect(invalidateFigureSnapshots).not.toHaveBeenCalled()
  })

  it('also invalidates a removed disk panel retained only in another canvas undo history', () => {
    vi.mocked(useDocumentStore.getState).mockReturnValue({
      buildProject: () => ({ canvases: [{ objects: [] }] }),
      past: [], future: [], txn: null,
      canvasSessions: { old: { past: [{ inverse: [{ op: 'add', path: ['objects', 0], value: {
        type: 'panel', fileId: 'Fig1.png', fileKind: 'raster', script: 'plot.py', overrides: [],
      } }] }] } },
    } as never)
    invalidateCapturedFigureSnapshots([{
      asset_id: 'runtime:Fig1', stem: 'Fig1', script: 'plot.py', original_artifact: 'Fig1.pdf',
    }] as CapturedFigureDescriptor[], 'project-a')
    expect(invalidateFigureSnapshots).toHaveBeenCalledWith(['runtime:Fig1', 'Fig1.pdf', 'Fig1.png'], 'project-a')
  })
})
