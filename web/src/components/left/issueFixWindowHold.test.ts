/**
 * 只读窗口（ADR 0181）里的一键修复：修复结果要写进文档（会被写入口的门挡住），后端热态却已经变了——
 * 所以不发起，记住，编辑权放开后再做。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { emptyProject } from '@/types/document'
import { heldEntryCount, resetHeldEntriesForTests } from '@/store/documentRestore'
import { setWindowHold, useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import type { ValidationIssue } from '@/lib/validation'
import { runBatchFix, runFix } from './IssueFixButton'

afterEach(() => {
  setWindowHold(null)
  resetHeldEntriesForTests()
})

describe('只读窗口里的一键修复', () => {
  it('runFix / runBatchFix 不发起（不进「正在修复」），各记住一次', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fix')
    setWindowHold({ role: 'yielded', docId: 'd_fix' })
    await runFix({} as ValidationIssue, undefined)
    await runBatchFix([])
    expect(useUiStore.getState().fixing).toBe(false)
    expect(heldEntryCount()).toBe(2)
  })
})
