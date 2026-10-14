/**
 * 桌面拥有的脚本停在 `input()` 上（P07，ADR 0126）：后端的 Pending 镜像进**同一个**答题队列，作答 / 停止走
 * `/api/native/sessions/*`。测的是「会话状态进来之后队列变成什么样、点了提交 / 停止发了什么请求」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    answerNativeInput: vi.fn().mockResolvedValue({ session: {} }),
    answerScriptInput: vi.fn().mockResolvedValue({}),
    cancelNativeSession: vi.fn().mockResolvedValue({ confirmed: true, session: {} }),
    stopScriptInput: vi.fn().mockResolvedValue({}),
    fetchScriptAnswers: vi.fn().mockResolvedValue({ scripts: {}, location: '', pending: [] }),
    fetchNativeSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  }
})
vi.mock('@/store/workspace', () => ({ addRuntimePanelToCanvas: vi.fn() }))

import {
  ApiError,
  answerNativeInput,
  answerScriptInput,
  cancelNativeSession,
  fetchNativeSessions,
  stopScriptInput,
  type NativeSessionInfo,
} from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useNativeSessionStore } from './nativeSessionStore'
import { useScriptInputStore } from './scriptInputStore'

const base = (over: Partial<NativeSessionInfo> = {}): NativeSessionInfo => ({
  session_id: 'native-owned-prep-1-abc',
  project_root: '/p',
  interpreter: '/py',
  interpreter_fingerprint: 'fp',
  target_kind: 'script',
  target_display: 'fig.py',
  cwd: '/p',
  arg_count: 0,
  python_version: '3.12',
  state: 'waiting_for_figure',
  barrier_reason: '',
  process_pid: 4242,
  stems: [],
  descriptors: [],
  script_error: null,
  terminal_error: null,
  exit_code: null,
  figures_captured: 0,
  started_at: 1,
  last_event_at: 1,
  sequence: 1,
  editable: false,
  owner: 'desktop',
  pending_input: null,
  ...over,
})

const asking = (seq: number, over = {}) =>
  base({
    sequence: seq,
    pending_input: {
      request_id: 'in-1-1',
      kind: 'input',
      prompt: 'Your name? ',
      masked: false,
      ordinal: 1,
      at: 1,
      ...over,
    },
  })

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentProjectId('p_a')
  useScriptInputStore.getState().clear()
  useNativeSessionStore.getState().clear()
})

describe('native pending input', () => {
  it('会话里有 Pending → 进答题队列（同一个队列）；答完（会话里没有了）→ 收起', () => {
    useNativeSessionStore.getState().applyEvent(asking(2))
    const q = useScriptInputStore.getState().queue
    expect(q).toHaveLength(1)
    expect(q[0]).toMatchObject({
      script: 'fig.py',
      input_kind: 'input',
      prompt: 'Your name? ',
      secret: false,
      native: { session_id: 'native-owned-prep-1-abc', request_id: 'in-1-1' },
    })
    // 重复到达只排一次
    useNativeSessionStore.getState().applyEvent(asking(3))
    expect(useScriptInputStore.getState().queue).toHaveLength(1)
    // 另一个窗口答了：会话状态里 Pending 没了，这里的框跟着收起
    useNativeSessionStore.getState().applyEvent(base({ sequence: 4 }))
    expect(useScriptInputStore.getState().queue).toHaveLength(0)
  })

  it('口令问：secret=true（密码框），而且后端 masked 才算数', () => {
    useNativeSessionStore
      .getState()
      .applyEvent(asking(2, { kind: 'getpass', masked: true, prompt: 'Token: ' }))
    expect(useScriptInputStore.getState().queue[0]).toMatchObject({
      input_kind: 'getpass',
      secret: true,
    })
  })

  it('提交走 native 作答接口、不走 /api/script_input；口令答案不进任何 store', async () => {
    useNativeSessionStore
      .getState()
      .applyEvent(asking(2, { kind: 'getpass', masked: true, prompt: 'Token: ' }))
    await useScriptInputStore.getState().submit('s3cret-value')
    expect(answerNativeInput).toHaveBeenCalledWith(
      'native-owned-prep-1-abc',
      'in-1-1',
      's3cret-value',
    )
    expect(answerScriptInput).not.toHaveBeenCalled()
    expect(JSON.stringify(useScriptInputStore.getState())).not.toContain('s3cret-value')
    expect(JSON.stringify(useNativeSessionStore.getState())).not.toContain('s3cret-value')
    expect(useScriptInputStore.getState().queue).toHaveLength(0)
  })

  it('「没有输入」= EOF：native 接口收到 null', async () => {
    useNativeSessionStore.getState().applyEvent(asking(2))
    await useScriptInputStore.getState().submit(null)
    expect(answerNativeInput).toHaveBeenCalledWith('native-owned-prep-1-abc', 'in-1-1', null)
  })

  it('别的窗口先答了（409）：收起，不留一个答不了的框，也不报错', async () => {
    vi.mocked(answerNativeInput).mockRejectedValueOnce(
      new ApiError('x', 409, { code: 'native_input_not_pending' }),
    )
    useNativeSessionStore.getState().applyEvent(asking(2))
    await useScriptInputStore.getState().submit('late')
    const s = useScriptInputStore.getState()
    expect(s.queue).toHaveLength(0)
    expect(s.error).toBeNull()
  })

  it('同一个 409 端点还会因断线返回 native_session_disconnected：框留着并显示原因（只有 not_pending 才收起）', async () => {
    vi.mocked(answerNativeInput).mockRejectedValueOnce(
      new ApiError('连接断了', 409, { code: 'native_session_disconnected' }),
    )
    useNativeSessionStore.getState().applyEvent(asking(2))
    await useScriptInputStore.getState().submit('x')
    const s = useScriptInputStore.getState()
    expect(s.queue).toHaveLength(1)
    expect(s.error).toContain('连接断了')
  })

  it('慢的 refresh 带回旧快照（已被更新的 SSE 清掉的问）：不复活已答的问', async () => {
    useNativeSessionStore.getState().applyEvent(base({ sequence: 5 }))
    vi.mocked(fetchNativeSessions).mockResolvedValueOnce({ sessions: [asking(3)] })
    await useNativeSessionStore.getState().refresh('/p')
    expect(useScriptInputStore.getState().queue).toHaveLength(0)
  })

  it('其它失败：框留着并显示原因（可以重试）', async () => {
    vi.mocked(answerNativeInput).mockRejectedValueOnce(new ApiError('连不上', 500, {}))
    useNativeSessionStore.getState().applyEvent(asking(2))
    await useScriptInputStore.getState().submit('x')
    const s = useScriptInputStore.getState()
    expect(s.queue).toHaveLength(1)
    expect(s.error).toContain('连不上')
    expect(s.busy).toBe(false)
  })

  it('「停止脚本」= 取消桌面拥有的会话（真终止），不是 stop_script_input', async () => {
    useNativeSessionStore.getState().applyEvent(asking(2))
    await useScriptInputStore.getState().stop()
    expect(cancelNativeSession).toHaveBeenCalledWith('native-owned-prep-1-abc')
    expect(stopScriptInput).not.toHaveBeenCalled()
  })

  it('重连 / 刷新：refresh 对账把还在等的问接回来', async () => {
    vi.mocked(fetchNativeSessions).mockResolvedValueOnce({ sessions: [asking(5)] })
    await useNativeSessionStore.getState().refresh('/p')
    expect(useScriptInputStore.getState().queue.map((q) => q.native?.request_id)).toEqual([
      'in-1-1',
    ])
  })

  it('老后端 / 终端拥有的会话没有 pending_input 字段：什么都不入队', () => {
    const old = base({ sequence: 2, owner: 'terminal' })
    delete (old as Partial<NativeSessionInfo>).pending_input
    useNativeSessionStore.getState().applyEvent(old)
    expect(useScriptInputStore.getState().queue).toHaveLength(0)
  })
})
