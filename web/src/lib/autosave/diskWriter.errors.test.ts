import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import { formatMessage, i18n, msg } from '@/i18n'
import { emptyProject } from '@/types/document'
import { createDiskWriter, saveIssueMessage, type SaveIssue } from './diskWriter'

async function rejectedWrite(error: unknown) {
  let issue: SaveIssue | null = null
  let drops = 0
  let writes = 0
  const outcomes: string[] = []
  const baselines: unknown[][] = []
  const writer = createDiskWriter({
    api: {
      put: async (_id, _pd, baseline, revision) => {
        baselines.push([baseline, revision])
        throw error
      },
      fetch: async () => null,
      fetchSummary: async () => null,
    },
    isCurrent: () => true,
    onSaving: () => {},
    onWritten: () => { writes += 1 },
    onFailed: (_id, failure) => { issue = failure },
    writeBlocked: () => false,
    dropLocalCopy: () => { drops += 1 },
    outcome: (_trigger, outcome) => outcomes.push(outcome),
  })
  writer.rememberRevision('working', { revision: 'original-revision' })
  writer.setBaseline('working', 7)
  writer.schedule('working', { ...emptyProject(), updatedAt: 8 }, null)
  await writer.whenIdle()
  writer.schedule('working', { ...emptyProject(), updatedAt: 9 }, null)
  await writer.whenIdle()
  return { issue: issue as SaveIssue | null, drops, writes, outcomes, baselines }
}

const generic = msg('autosave.diskFailed', undefined, 'workspace')

describe('保存失败的结构化原因', () => {
  it('422 保留后端 code/params/fallback，不带无关响应体，不推进基线或清本机副本', async () => {
    const result = await rejectedWrite(new ApiError('输入附件校验失败', 422, {
      code: 'chart_source_mismatch', params: { source: '图一.svg' }, diagnostic: 'not-for-save-state',
    }))
    expect(result.issue).toEqual({
      kind: 'backend', docId: 'working',
      backend: { status: 422, code: 'chart_source_mismatch', params: { source: '图一.svg' }, fallback: '输入附件校验失败' },
    })
    expect(formatMessage(saveIssueMessage(result.issue, generic))).toBe('输入附件校验失败')
    expect(result).toMatchObject({ drops: 0, writes: 0, outcomes: ['failed', 'failed'] })
    expect(result.baselines).toEqual([[7, 'original-revision'], [7, 'original-revision']])
  })

  it('已登记 code 的参数延迟翻译，常驻错误切语言后仍更新', async () => {
    const { issue } = await rejectedWrite(new ApiError('fallback', 422, {
      code: 'non_finite_number', params: { reason: 'NaN @ x' },
    }))
    const message = saveIssueMessage(issue, generic)
    expect(formatMessage(message)).toContain('NaN @ x')
    expect(formatMessage(message)).toContain('无法保存的数值')
    await i18n.changeLanguage('en-US')
    expect(formatMessage(message)).toContain('NaN @ x')
    expect(formatMessage(message)).not.toContain('无法保存的数值')
  })

  it.each([
    [403, 'layout_read_only', {}, '只读'],
    [500, 'write_failed', { reason: 'ENOSPC' }, 'ENOSPC'],
  ])('%s 后端明确原因沿统一错误翻译，不变成校验或冲突', async (status, code, params, fragment) => {
    const { issue, outcomes } = await rejectedWrite(new ApiError('fallback', status, { code, params }))
    expect(issue?.kind).toBe('backend')
    expect(formatMessage(saveIssueMessage(issue, generic))).toContain(fragment)
    expect(outcomes).toEqual(['failed', 'failed'])
  })

  it.each([new TypeError('Failed to fetch'), new Error('disk full'), new ApiError('plain 500', 500, {})])(
    '无结构 I/O 或网络错误保留通用提示，不透出本地异常', async (error) => {
      const { issue } = await rejectedWrite(error)
      expect(issue).toBeNull()
      expect(saveIssueMessage(issue, generic)).toBe(generic)
    },
  )

  it.each([['stale_write', 'stale'], ['external_change', 'external']])(
    '409 %s 仍是原冲突，保持磁盘摘要/基线/本机副本', async (code, kind) => {
      const { issue, outcomes, drops, writes, baselines } = await rejectedWrite(new ApiError('conflict', 409, {
        code, revision: 'their-revision', summary: { revision: 'their-revision', objects: 2 },
      }))
      expect(issue).toMatchObject({ kind, docId: 'working', disk: { revision: 'their-revision', objects: 2 } })
      expect(outcomes).toEqual(['conflict', 'conflict'])
      expect(drops).toBe(0)
      expect(writes).toBe(0)
      expect(baselines).toEqual([[7, 'original-revision'], [7, 'original-revision']])
    },
  )
})
