import { expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { emptyProject } from '@/types/document'
import { createDiskWriter, type DiskSaveContext, type DiskWriterPorts } from './diskWriter'

it('排队后基线变化也不能洗掉该正文观察的recovery proof/revision，失败重试逐字保留', async () => {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const put = vi.fn<DiskWriterPorts['api']['put']>(async id => {
    if (id === 'blocker') { await gate; return { revision: 'blocker-revision' } }
    throw new ApiError('conflict', 409, { code: 'stale_write' })
  })
  const onFailed = vi.fn()
  const writer = createDiskWriter({
    api: { put, fetch: async () => null, fetchSummary: async () => null },
    isCurrent: () => true, onSaving: () => {}, onWritten: () => {}, onFailed,
    writeBlocked: () => false, dropLocalCopy: () => {}, outcome: () => {},
  })
  const document = emptyProject()
  const proof = { revision: 'observed-revision', expected: { version: 1 as const, status: 'ok' as const,
    binding: null, intent_sequence: 0, edit: { axis: null, autosave_revision: 'old-tracked', known: false } } }
  const context: DiskSaveContext = {
    documentId: 'A', loadSeq: 1, pj: 'project', document, sourceGenerations: new Map(), ownerGenerations: new Map(), receiptGeneration: 0,
    projectFileContext: { address: { projectId: 'project', documentId: 'A', format: 'legacy' }, generation: 1,
      axis: { editor_id: 'new-branch', sequence: 1 }, base_axis: null }, projectFileRecovery: proof,
  }
  writer.rememberRevision('blocker', null)
  writer.schedule('blocker', document, 'project')
  writer.setRevision('A', 'observed-revision')
  writer.schedule('A', document, 'project', context)
  writer.setRevision('A', 'newer-revision')
  release(); await writer.whenIdle()
  writer.schedule('A', document, 'project', context)
  await writer.whenIdle()
  const calls = put.mock.calls.filter(call => call[0] === 'A')
  expect(calls).toHaveLength(2)
  for (const call of calls) {
    expect(call[3]).toBe('observed-revision')
    expect(call[5]).toEqual({ projectFileEdit: { axis: context.projectFileContext!.axis, base_axis: null, base_revision: 'observed-revision' },
      projectFileRecovery: proof })
  }
  expect(onFailed).toHaveBeenCalledTimes(2)
  expect(writer.revisionOf('A')).toBe('newer-revision')
})
