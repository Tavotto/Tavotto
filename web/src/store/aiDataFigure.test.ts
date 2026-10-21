/**
 * 从数据出图（TAF Phase 5c）的 store 动作：立即放一轮「进行中」的本地会话，同步 HTTP 回来后按 `task.outcome` 收尾。
 * 无脚本、无后端会话：不锁发送、不进对账；换代后回来的结果丢弃。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, aiDataFigure } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { setCurrentProjectId } from '@/lib/session'
import { t } from '@/i18n'
import { isOpenSession, sessionAgentLabel, useAiStore } from './aiStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFigure: vi.fn(),
}))
const call = vi.mocked(aiDataFigure)

const only = () => {
  const list = useAiStore.getState().sessions
  expect(list).toHaveLength(1)
  return list[0]
}

beforeEach(() => {
  call.mockReset()
  setCurrentProjectId('pa')
  useAiStore.getState().clear()
})

describe('startDataFigure', () => {
  it('立即放一轮进行中的本地会话；品牌名做执行者；不锁任何脚本', async () => {
    let release!: (v: Awaited<ReturnType<typeof aiDataFigure>>) => void
    call.mockReturnValue(new Promise((r) => (release = r)))
    const p = useAiStore.getState().startDataFigure('data/a.csv', '画 y 随 x 的变化')
    const s = only()
    expect(s).toMatchObject({
      agent: 'tavotto',
      prompt: '画 y 随 x 的变化',
      script: '',
      panelId: null,
      fileId: null,
      gid: null,
      scope: 'figure',
      target: 'data/a.csv',
      status: 'running',
      project: 'pa',
      task: { task_type: 'data_to_figure', outcome: null },
    })
    expect(s.id.startsWith('df-')).toBe(true)
    expect(sessionAgentLabel(null, s)).toBe(PRODUCT_NAME)
    expect(call.mock.calls[0][0]).toMatchObject({ data: 'data/a.csv', instruction: '画 y 随 x 的变化' })
    release({ task: { outcome: 'completed', verification: { unmet: [] } }, script: 'data/a.py' })
    await p
  })

  it('completed：done、changed、带 script，终态已落定（不再被当作未结会话）', async () => {
    call.mockResolvedValue({ task: { outcome: 'completed', verification: { unmet: [] } }, script: 'data/a.py' })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    const s = only()
    expect(s).toMatchObject({ status: 'done', changed: true, finalized: true })
    expect(s.task).toMatchObject({ task_type: 'data_to_figure', outcome: 'completed', script: 'data/a.py', unmet: [] })
    expect(s.finishedAt).toBeTypeOf('number')
    expect(isOpenSession(s)).toBe(false)
  })

  it('partial：done + changed，unmet 带上', async () => {
    const unmet = [{ constraint_id: 'data.rows', reason_code: 'non_numeric' }]
    call.mockResolvedValue({ task: { outcome: 'partial', verification: { unmet } }, script: 'data/a.py' })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    expect(only()).toMatchObject({ status: 'done', changed: true })
    expect(only().task).toMatchObject({ outcome: 'partial', unmet })
  })

  it('needs_input：done 但没改动；preview 随任务带回', async () => {
    call.mockResolvedValue({
      task: { outcome: 'needs_input', verification: {} },
      preview: { x: 'a', y: ['b'] },
    })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    const s = only()
    expect(s).toMatchObject({ status: 'done', changed: false, finalized: true })
    expect(s.task).toMatchObject({ outcome: 'needs_input', unmet: [], preview: { x: 'a', y: ['b'] } })
    expect(s.task?.script).toBeUndefined()
  })

  it('failed 结局：failed，error = task.error', async () => {
    call.mockResolvedValue({ task: { outcome: 'failed', error: 'boom', verification: {} } })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    expect(only()).toMatchObject({ status: 'failed', changed: false, error: 'boom' })
  })

  it('HTTP 400 带 code：failed，文案是该 code 的翻译，不拼中文', async () => {
    call.mockRejectedValue(new ApiError('原文', 400, { code: 'data_not_found' }))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    const s = only()
    expect(s).toMatchObject({ status: 'failed', finalized: true })
    expect(s.error).toBe(t('backend.data_not_found', { ns: 'errors' }))
    expect(s.task).toMatchObject({ task_type: 'data_to_figure', outcome: 'failed' })
  })

  it('换代（切项目）后回来的结果丢弃：旧会话随 clear 没了，新代里也不出现', async () => {
    let release!: (v: Awaited<ReturnType<typeof aiDataFigure>>) => void
    call.mockReturnValue(new Promise((r) => (release = r)))
    const p = useAiStore.getState().startDataFigure('data/a.csv', '画')
    expect(useAiStore.getState().sessions).toHaveLength(1)
    useAiStore.getState().clear()
    release({ task: { outcome: 'completed', verification: { unmet: [] } }, script: 'data/a.py' })
    await p
    expect(useAiStore.getState().sessions).toHaveLength(0)
  })

  it('代际变了但会话还在列表里：回来的结果也不改写它（守卫本身，不靠 clear 清空）', async () => {
    let release!: (v: Awaited<ReturnType<typeof aiDataFigure>>) => void
    call.mockReturnValue(new Promise((r) => (release = r)))
    const p = useAiStore.getState().startDataFigure('data/a.csv', '画')
    useAiStore.setState((s) => ({ generation: s.generation + 1 }))
    release({ task: { outcome: 'completed', verification: { unmet: [] } }, script: 'data/a.py' })
    await p
    expect(only()).toMatchObject({ status: 'running', changed: false })
    expect(only().finalized).toBeUndefined()
  })

  it('换代后回来的失败也丢弃', async () => {
    let fail!: (e: unknown) => void
    call.mockReturnValue(new Promise((_, rej) => (fail = rej)))
    const p = useAiStore.getState().startDataFigure('data/a.csv', '画')
    useAiStore.getState().clear()
    fail(new ApiError('x', 400, { code: 'data_not_found' }))
    await p
    expect(useAiStore.getState().sessions).toHaveLength(0)
  })
})
