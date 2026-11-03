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

  it('failed 但带 script（提交后验证失败，文件已建）：如实记「已写入」——done + changed + script，不当成未改动', async () => {
    const unmet = [{ constraint_id: 'data.voltage_mV', reason_code: 'data_mismatch' }]
    call.mockResolvedValue({ task: { outcome: 'failed', error: '核对没通过', verification: { unmet } }, script: 'data/a.py' })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    const s = only()
    expect(s).toMatchObject({ status: 'done', changed: true, finalized: true, error: '核对没通过' })
    expect(s.task).toMatchObject({ outcome: 'failed', script: 'data/a.py', unmet })
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

describe('结果不确定（同步 HTTP 在后端可能已提交之后断了）', () => {
  it.each([
    ['网络错误', () => new TypeError('Failed to fetch')],
    ['5xx 网关错（无 code）', () => new ApiError('bad gateway', 502, {})],
    ['超时', () => new DOMException('timeout', 'AbortError')],
  ])('%s：unconfirmed，记住 rid / data / 指令，不是确定失败', async (_n, mk) => {
    call.mockRejectedValue(mk())
    await useAiStore.getState().startDataFigure('data/a.csv', '画 y 随 x')
    const s = only()
    const rid = call.mock.calls[0][0].client_request_id
    expect(s).toMatchObject({
      status: 'failed',
      unconfirmed: true,
      requestId: rid,
      target: 'data/a.csv',
      prompt: '画 y 随 x',
      finalized: true,
    })
    expect(s.error).toBeUndefined()
  })

  it('4xx：确定失败（不带 unconfirmed）', async () => {
    call.mockRejectedValue(new ApiError('原文', 404, { code: 'taf_disabled' }))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    expect(only().unconfirmed).toBeFalsy()
    expect(only().status).toBe('failed')
  })

  it('查询结果：同一个 rid 再调一次，按结果收尾（后端已执行过 = 去重响应带 script）', async () => {
    call.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    const first = call.mock.calls[0][0]
    call.mockResolvedValueOnce({
      task: { outcome: 'completed', deduped: true, final: true, verification: { unmet: [] } },
      script: 'data/a_figure.py',
    })
    await useAiStore.getState().reconcileDataFigure(only().id)
    expect(call).toHaveBeenCalledTimes(2)
    expect(call.mock.calls[1][0]).toEqual(first) // data / instruction / client_request_id 全同
    expect(only()).toMatchObject({ status: 'done', changed: true, unconfirmed: false, finalized: true })
    expect(only().task).toMatchObject({ outcome: 'completed', script: 'data/a_figure.py' })
  })

  it('查询本身又不确定：保持 unconfirmed，仍可再查', async () => {
    call.mockRejectedValue(new TypeError('Failed to fetch'))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    await useAiStore.getState().reconcileDataFigure(only().id)
    expect(only()).toMatchObject({ status: 'failed', unconfirmed: true })
    expect(call.mock.calls[1][0].client_request_id).toBe(call.mock.calls[0][0].client_request_id)
  })

  it('去重响应指向一个还在跑的任务（final=false）：仍然不确定', async () => {
    call.mockRejectedValueOnce(new TypeError('x'))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    call.mockResolvedValueOnce({ task: { outcome: null, deduped: true, final: false } })
    await useAiStore.getState().reconcileDataFigure(only().id)
    expect(only().unconfirmed).toBe(true)
  })

  it('查询期间换代：结果丢弃', async () => {
    call.mockRejectedValueOnce(new TypeError('x'))
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    let release!: (v: Awaited<ReturnType<typeof aiDataFigure>>) => void
    call.mockReturnValueOnce(new Promise((r) => (release = r)))
    const p = useAiStore.getState().reconcileDataFigure(only().id)
    useAiStore.setState((s) => ({ generation: s.generation + 1 }))
    release({ task: { outcome: 'completed', verification: { unmet: [] } }, script: 'data/a.py' })
    await p
    expect(only()).toMatchObject({ status: 'running' })
  })

  it('没有 unconfirmed 的会话不能查询（不会凭空再发请求）', async () => {
    call.mockResolvedValue({ task: { outcome: 'completed', verification: {} }, script: 'data/a.py' })
    await useAiStore.getState().startDataFigure('data/a.csv', '画')
    await useAiStore.getState().reconcileDataFigure(only().id)
    expect(call).toHaveBeenCalledTimes(1)
  })
})
