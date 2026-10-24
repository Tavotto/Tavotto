/**
 * AI 修复的前端状态（ADR 0148 / 0149）。判据的主语：**发出去的请求**（`requestAiFix` / `answerAiFix` 的入参）与
 * **store 里条目的阶段**；后端用假的，不联网。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  requestAiFix: vi.fn(),
  fetchAiFix: vi.fn(),
  answerAiFix: vi.fn(),
  cancelAiFix: vi.fn(),
}))

import { answerAiFix, ApiError, cancelAiFix, fetchAiFix, requestAiFix, type AiFixState } from '@/lib/api'
import { useAiFixStore } from '@/store/aiFixStore'
import { useAiStore } from '@/store/aiStore'
import { useConvertStore } from '@/store/convertStore'
import { useConnectAiStore } from '@/store/connectAiStore'
import { useUiStore } from '@/store/uiStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { setCurrentProjectId } from '@/lib/session'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'

const KEY = 'script:plot.py'
const REF = { session_id: 'psess-1', script: 'plot.py', attemptId: 'a1' }

const state = (patch: Partial<AiFixState>): AiFixState => ({
  fix_id: 'fx_1',
  state: 'running',
  script: 'plot.py',
  session_id: 'psess-1',
  card: null,
  outcome: null,
  summary: '',
  reason: '',
  steps: 0,
  seq: 1,
  ...patch,
})
const card = (patch: object) => ({ id: 'c1', kind: 'approve', summary: '把 numpy 降到 1.26', ...patch }) as never

beforeEach(() => {
  vi.mocked(requestAiFix).mockReset()
  vi.mocked(fetchAiFix).mockReset()
  vi.mocked(answerAiFix).mockReset()
  vi.mocked(cancelAiFix).mockReset()
  useAiFixStore.getState().reset()
  useAiStore.setState({ caps: { agents: [{ id: 'claude', usable: true }], endpoints: [], presets: [], checked_at_ms: 0 } as never })
})
afterEach(() => {
  useAiFixStore.getState().reset()
  useAiStore.setState({ caps: null })
  useDiagSendStore.setState({ open: false, presetNote: '', sentCount: 0 })
})

const start = async () => {
  vi.mocked(requestAiFix).mockResolvedValue({ ok: true, fix_id: 'fx_1' })
  vi.mocked(fetchAiFix).mockResolvedValue(state({}))
  await useAiFixStore.getState().startAiFix(KEY, REF)
}
const entry = () => useAiFixStore.getState().entries[KEY]

describe('开始', () => {
  it('后端回会话 id：进入 running 并重读一次状态', async () => {
    await start()
    expect(requestAiFix).toHaveBeenCalledWith({ session_id: 'psess-1', script: 'plot.py' })
    expect(entry()?.phase).toBe('running')
    expect(entry()?.fixId).toBe('fx_1')
    expect(fetchAiFix).toHaveBeenCalledWith('fx_1', undefined)
  })

  it('ai_fix_unavailable / 请求失败 → unavailable；ai_fix_busy → busy', async () => {
    vi.mocked(requestAiFix).mockResolvedValue({ ok: false, reason: 'ai_fix_unavailable' })
    await useAiFixStore.getState().startAiFix(KEY, REF)
    expect(entry()?.phase).toBe('unavailable')
    vi.mocked(requestAiFix).mockResolvedValue({ ok: false, reason: 'ai_fix_busy' })
    await useAiFixStore.getState().startAiFix(KEY, REF)
    expect(entry()?.phase).toBe('busy')
    vi.mocked(requestAiFix).mockRejectedValue(new Error('offline'))
    await useAiFixStore.getState().startAiFix(KEY, REF)
    expect(entry()?.phase).toBe('unavailable')
  })

  it('capabilities 说不可用：不发请求，直接 unavailable', async () => {
    useAiStore.setState({ caps: { agents: [], ai_fix_available: false } as never })
    await useAiFixStore.getState().startAiFix(KEY, REF)
    expect(requestAiFix).not.toHaveBeenCalled()
    expect(entry()?.phase).toBe('unavailable')
  })
})

describe('卡片', () => {
  it('SSE 提示触发重读；等用户点的卡落进条目，回答带 card_id', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({}) }))
    useAiFixStore.getState().onHint('fx_1')
    await vi.waitFor(() => expect(entry()?.phase).toBe('waiting'))
    expect(entry()?.card?.summary).toBe('把 numpy 降到 1.26')
    vi.mocked(answerAiFix).mockResolvedValue(state({}))
    vi.mocked(fetchAiFix).mockResolvedValue(state({}))
    await useAiFixStore.getState().answer(KEY, 'yes')
    expect(answerAiFix).toHaveBeenCalledWith('fx_1', { card_id: 'c1', answer: 'yes' }, undefined)
    expect(entry()?.phase).toBe('running')
  })

  it('别的会话的提示不动本条目', async () => {
    await start()
    vi.mocked(fetchAiFix).mockClear()
    useAiFixStore.getState().onHint('fx_other')
    expect(fetchAiFix).not.toHaveBeenCalled()
  })

  it('选择题的「我自己填」把文字带上', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({ kind: 'choice', allow_other: true }) }))
    await useAiFixStore.getState().refresh(KEY)
    vi.mocked(answerAiFix).mockResolvedValue(state({}))
    await useAiFixStore.getState().answer(KEY, 'other', '~/lab')
    expect(answerAiFix).toHaveBeenCalledWith('fx_1', { card_id: 'c1', answer: 'other', text: '~/lab' }, undefined)
  })

  it('回答期间不重复提交（防连点）', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({}) }))
    await useAiFixStore.getState().refresh(KEY)
    let release: (v: AiFixState) => void = () => {}
    vi.mocked(answerAiFix).mockReturnValue(new Promise((r) => (release = r)))
    const first = useAiFixStore.getState().answer(KEY, 'yes')
    await useAiFixStore.getState().answer(KEY, 'no')
    expect(answerAiFix).toHaveBeenCalledTimes(1)
    release(state({}))
    await first
  })
})

describe('「发送」卡', () => {
  it('打开发送对话框只预填说明（源码勾选不在这里），用户真的发送成功才回答 sent', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({ kind: 'send', summary: 'ROOT 6.38 没有 3.14 的编译版' }) }))
    await useAiFixStore.getState().refresh(KEY)
    useAiFixStore.getState().openSend(KEY)
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useDiagSendStore.getState().takePresetNote()).toBe('ROOT 6.38 没有 3.14 的编译版')
    expect(answerAiFix).not.toHaveBeenCalled() // 光是打开对话框不算发送
    vi.mocked(answerAiFix).mockResolvedValue(state({}))
    useDiagSendStore.getState().markSent()
    await vi.waitFor(() => expect(answerAiFix).toHaveBeenCalledWith('fx_1', { card_id: 'c1', answer: 'sent' }, undefined))
  })

  it('没点过「查看并发送」时别处的发送成功不会被当成这张卡的回答', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({ kind: 'send' }) }))
    await useAiFixStore.getState().refresh(KEY)
    useDiagSendStore.getState().markSent()
    await Promise.resolve()
    expect(answerAiFix).not.toHaveBeenCalled()
  })
})

describe('结束', () => {
  it('fixed：清掉条目并重跑准备会话验证（不信 AI 的一面之词）', async () => {
    await start()
    const open = vi.fn(async () => {})
    const target = { script: 'plot.py' } as never
    useProjectPreparationStore.setState({ entries: { [KEY]: { target } } as never, open } as never)
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'finished', outcome: 'fixed', summary: '好了' }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toBeUndefined()
    expect(open).toHaveBeenCalledWith(target, { run: true, rerun: true })
  })

  it('convert：清掉修复条目、记下交接、启动既有的「生成可编辑版本」；不重跑准备会话', async () => {
    await start()
    const open = vi.fn(async () => {})
    useProjectPreparationStore.setState({ open } as never)
    const convertStart = vi.fn(async () => {})
    useConvertStore.setState({ start: convertStart } as never)
    vi.mocked(fetchAiFix).mockResolvedValue(
      state({ state: 'finished', outcome: 'convert', summary: '生成可编辑版本', handoff: { kind: 'convert', script: 'plot.py', library: 'pillow', path: 'out/plot.png' } }),
    )
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toBeUndefined()
    expect(useAiFixStore.getState().handoffs[KEY]).toEqual({ script: 'plot.py', library: 'pillow', path: 'out/plot.png' })
    expect(convertStart).toHaveBeenCalledWith('plot.py', 'pillow', 'out/plot.png', 'python')
    expect(open).not.toHaveBeenCalled()
  })

  it('交接记录随转换记录消失而清掉；切项目 / reset 也清', async () => {
    useAiFixStore.setState({ handoffs: { [KEY]: { script: 'plot.py', library: 'pillow', path: null } } })
    useConvertStore.setState({ entries: { 'plot.py': { phase: 'ready' } } as never })
    useConvertStore.setState({ entries: {} })
    expect(useAiFixStore.getState().handoffs[KEY]).toBeUndefined()
    useAiFixStore.setState({ handoffs: { [KEY]: { script: 'a.py', library: 'opencv', path: null } } })
    useAiFixStore.getState().reset()
    expect(useAiFixStore.getState().handoffs).toEqual({})
  })

  it('not_fixed / failed：落成 done 带一句话，不自动重跑；知道了 = 清掉', async () => {
    await start()
    const open = vi.fn(async () => {})
    useProjectPreparationStore.setState({ open } as never)
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'finished', outcome: 'not_fixed', summary: '装不上' }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toMatchObject({ phase: 'done', outcome: 'not_fixed', summary: '装不上' })
    expect(open).not.toHaveBeenCalled()
    useAiFixStore.getState().clear(KEY)
    expect(entry()).toBeUndefined()
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'finished', outcome: 'failed', reason: 'quota' }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toMatchObject({ phase: 'done', outcome: 'failed', reason: 'quota' })
  })

  it('取消：请求后端取消；cancelled 直接收起', async () => {
    await start()
    vi.mocked(cancelAiFix).mockResolvedValue(state({}))
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'finished', outcome: 'cancelled' }))
    await useAiFixStore.getState().cancel(KEY)
    expect(cancelAiFix).toHaveBeenCalledWith('fx_1', undefined)
    expect(entry()).toBeUndefined()
  })

  it('后端不认这个会话了（404，重启丢了它）：回到原来的失败卡，不卡在转圈', async () => {
    await start()
    vi.mocked(fetchAiFix).mockRejectedValue(new ApiError('gone', 404, {}))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toBeUndefined()
  })
})

describe('切项目竞态', () => {
  afterEach(() => setCurrentProjectId(null))

  it('POST 未返回时切到 B：A 的结果不落进 store，已起来的修复按 A 的项目 id 取消', async () => {
    setCurrentProjectId('pjA')
    let release: (v: { ok: true; fix_id: string }) => void = () => {}
    vi.mocked(requestAiFix).mockReturnValue(new Promise((r) => (release = r)))
    vi.mocked(cancelAiFix).mockResolvedValue(state({}))
    const p = useAiFixStore.getState().startAiFix(KEY, REF)
    setCurrentProjectId('pjB')
    release({ ok: true, fix_id: 'fx_A' })
    await p
    expect(entry()).toBeUndefined()
    expect(cancelAiFix).toHaveBeenCalledWith('fx_A', 'pjA')
    expect(fetchAiFix).not.toHaveBeenCalled()
  })

  it('探测本机 AI（loadCaps）在途时 A → B → A：这次点击作废，不落 store、不发修复请求', async () => {
    setCurrentProjectId('pjA')
    let release: () => void = () => {}
    const loadCaps = vi.fn(
      () =>
        new Promise<void>((r) => {
          release = () => {
            useAiStore.setState({ caps: { agents: [{ id: 'claude', usable: true }], endpoints: [], presets: [], checked_at_ms: 0 } as never })
            r()
          }
        }),
    )
    const realLoadCaps = useAiStore.getState().loadCaps
    useAiStore.setState({ caps: null, loadCaps } as never)
    const p = useAiFixStore.getState().request(KEY, REF)
    setCurrentProjectId('pjB')
    setCurrentProjectId('pjA')
    release()
    await p
    expect(loadCaps).toHaveBeenCalled()
    expect(entry()).toBeUndefined()
    expect(requestAiFix).not.toHaveBeenCalled()
    useAiStore.setState({ loadCaps: realLoadCaps })
  })

  it('A → B → A 回到同一个 id 也已换代：旧请求仍然丢弃', async () => {
    setCurrentProjectId('pjA')
    let release: (v: { ok: true; fix_id: string }) => void = () => {}
    vi.mocked(requestAiFix).mockReturnValue(new Promise((r) => (release = r)))
    vi.mocked(cancelAiFix).mockResolvedValue(state({}))
    const p = useAiFixStore.getState().startAiFix(KEY, REF)
    setCurrentProjectId('pjB')
    setCurrentProjectId('pjA')
    release({ ok: true, fix_id: 'fx_A' })
    await p
    expect(entry()).toBeUndefined()
  })

  it('在跑的修复，切走时被取消（带发起时的项目 id），条目清空；轮询返回的旧结果不落地', async () => {
    setCurrentProjectId('pjA')
    vi.mocked(requestAiFix).mockResolvedValue({ ok: true, fix_id: 'fx_A' })
    let release: (v: AiFixState) => void = () => {}
    vi.mocked(fetchAiFix).mockReturnValue(new Promise((r) => (release = r)))
    vi.mocked(cancelAiFix).mockResolvedValue(state({}))
    await useAiFixStore.getState().startAiFix(KEY, REF)
    expect(entry()?.pj).toBe('pjA')
    setCurrentProjectId('pjB')
    expect(cancelAiFix).toHaveBeenCalledWith('fx_A', 'pjA')
    expect(entry()).toBeUndefined()
    release(state({ state: 'waiting', card: card({}) }))
    await Promise.resolve()
    await Promise.resolve()
    expect(entry()).toBeUndefined() // 重读的结果不会把 A 的卡片写进 B
  })

  it('读状态与回答都带发起时的项目 id', async () => {
    setCurrentProjectId('pjA')
    vi.mocked(requestAiFix).mockResolvedValue({ ok: true, fix_id: 'fx_A' })
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'waiting', card: card({}) }))
    await useAiFixStore.getState().startAiFix(KEY, REF)
    await vi.waitFor(() => expect(entry()?.phase).toBe('waiting'))
    expect(fetchAiFix).toHaveBeenCalledWith('fx_A', 'pjA')
    vi.mocked(answerAiFix).mockResolvedValue(state({}))
    await useAiFixStore.getState().answer(KEY, 'yes')
    expect(answerAiFix).toHaveBeenCalledWith('fx_A', { card_id: 'c1', answer: 'yes' }, 'pjA')
  })
})


describe('进度与卡住的落地', () => {
  it('重读时把 activity / stalled 带进条目；恢复后清掉', async () => {
    await start()
    const stalled = { reason: 'cli_waiting_approval', detail: 'change_python' } as const
    const activity = { tool: 'try_run', phase: 'start', detail: '', at_ms: 5 } as const
    vi.mocked(fetchAiFix).mockResolvedValue(state({ activity, stalled }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toMatchObject({ phase: 'running', activity, stalled })
    vi.mocked(fetchAiFix).mockResolvedValue(state({ activity, stalled: null }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry().stalled).toBeUndefined()
  })

  it('自动停止（failed + 卡住原因码）落成 done 并保留原因码', async () => {
    await start()
    vi.mocked(fetchAiFix).mockResolvedValue(state({ state: 'finished', outcome: 'failed', reason: 'cli_tool_not_delivered' }))
    await useAiFixStore.getState().refresh(KEY)
    expect(entry()).toMatchObject({ phase: 'done', outcome: 'failed', reason: 'cli_tool_not_delivered' })
  })
})

describe('没连 AI → 连接小弹窗（不再打开设置页）', () => {
  const CONNECT_VIEW = { agents: [], endpoints: [], presets: [], vendors: [], connections: [], checked_at_ms: 0 } as never
  const connected = {
    ...(CONNECT_VIEW as object),
    connections: [{ id: 'deepseek', vendor: 'deepseek', label: 'DeepSeek', logo: 'deepseek', state: 'ready', models: [], default_model: null }],
  } as never

  beforeEach(() => {
    useConnectAiStore.setState({ open: false, reason: 'general', onConnected: null })
    useUiStore.setState({ settingsOpen: false, settingsSection: null })
    useAiStore.setState({ caps: CONNECT_VIEW })
  })
  afterEach(() => useConnectAiStore.setState({ open: false, onConnected: null }))

  it('打开的是连接弹窗（reason=fix），设置页不开；条目停在 awaiting_connect', async () => {
    await useAiFixStore.getState().request(KEY, REF)
    expect(useConnectAiStore.getState()).toMatchObject({ open: true, reason: 'fix' })
    expect(useUiStore.getState().settingsOpen).toBe(false)
    expect(entry().phase).toBe('awaiting_connect')
    expect(requestAiFix).not.toHaveBeenCalled()
  })

  it('连好（出现 ready 的连接）后自动续跑，请求入参不变', async () => {
    await useAiFixStore.getState().request(KEY, REF)
    vi.mocked(requestAiFix).mockResolvedValue({ ok: true, fix_id: 'fx_1' })
    vi.mocked(fetchAiFix).mockResolvedValue(state({}))
    useAiStore.setState({ caps: connected })
    await vi.waitFor(() => expect(requestAiFix).toHaveBeenCalledWith({ session_id: 'psess-1', script: 'plot.py' }))
    expect(entry().phase).toBe('running')
  })

  it('弹窗关了仍没连上：放弃等待，之后偶然连上也不会冷不丁开始修', async () => {
    await useAiFixStore.getState().request(KEY, REF)
    useConnectAiStore.getState().close()
    expect(entry()).toBeUndefined()
    useAiStore.setState({ caps: connected })
    await Promise.resolve()
    expect(requestAiFix).not.toHaveBeenCalled()
  })

  it('弹窗关闭时已经连上了：等待的那条不被清掉（由 caps 订阅接走）', async () => {
    await useAiFixStore.getState().request(KEY, REF)
    vi.mocked(requestAiFix).mockResolvedValue({ ok: true, fix_id: 'fx_1' })
    vi.mocked(fetchAiFix).mockResolvedValue(state({}))
    useAiStore.setState({ caps: connected })
    useConnectAiStore.getState().close()
    await vi.waitFor(() => expect(entry()?.phase).toBe('running'))
  })

  it('降级：老后端没有连接视图 → 仍打开设置页的 AI 分区，不开空弹窗', async () => {
    useAiStore.setState({ caps: { agents: [], endpoints: [], presets: [], checked_at_ms: 0 } as never })
    await useAiFixStore.getState().request(KEY, REF)
    expect(useConnectAiStore.getState().open).toBe(false)
    expect(useUiStore.getState()).toMatchObject({ settingsOpen: true, settingsSection: 'ai' })
  })
})
