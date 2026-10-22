import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  startConvert: vi.fn(),
  fetchAiSession: vi.fn(),
  fetchConvert: vi.fn(),
  adoptConvert: vi.fn(),
  discardConvert: vi.fn(),
  resolveConvertDependencies: vi.fn(),
  replanConvertDependencies: vi.fn(),
  aiCancel: vi.fn().mockResolvedValue({ ok: true }),
}))

import { ApiError, aiCancel, discardConvert, fetchAiSession, fetchConvert, replanConvertDependencies, resolveConvertDependencies, startConvert } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { POLL_MS, useConvertStore } from '@/store/convertStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'

const start = () => useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
const ridOf = (n: number) => vi.mocked(startConvert).mock.calls[n]![0].client_request_id

describe('convertStore', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset()
    vi.mocked(aiCancel).mockClear()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue({ status: 'running', final: false } as never)
  })
  afterEach(() => {
    useConvertStore.getState().clear('plot.py')
    vi.useRealTimers()
  })

  it('未决请求 id 按项目分作用域：B 的同名脚本成功不会冲掉 A 的未决 id', async () => {
    vi.mocked(startConvert).mockRejectedValueOnce(new Error('network down'))
    await start()
    expect(useConvertStore.getState().entries['plot.py']?.phase).toBe('failed')
    const ridA = ridOf(0)

    setCurrentProjectId('B')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'sB', script: 'plot.py' })
    const b = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(1)).not.toBe(ridA)
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await b

    setCurrentProjectId('A')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'sA', script: 'plot.py' })
    const a = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(2)).toBe(ridA)
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await a
  })

  it('停止后立刻重试：旧 POST 晚到的失败不覆盖新会话的 running 条目', async () => {
    let rejectOld!: (e: Error) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((_r, rej) => (rejectOld = rej)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'new1', script: 'plot.py' })
    const second = start()
    await vi.advanceTimersByTimeAsync(POLL_MS - 1)
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', session: 'new1' })
    rejectOld(new Error('late failure'))
    await first
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', session: 'new1', error: null })
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await second
  })

  it('停止后立刻重试复用同一请求 id 时，旧 POST 晚到的成功不取消后端去重出的同一会话', async () => {
    let resolveOld!: (v: { session: string; script: string }) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'same', script: 'plot.py' })
    const second = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(1)).toBe(ridOf(0))
    resolveOld({ session: 'same', script: 'plot.py' })
    await first
    expect(aiCancel).not.toHaveBeenCalled()
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await second
  })

  it('旧 POST（同一 id）先成功不清 id：新 attempt 模糊失败后再重试仍是原 id', async () => {
    let resolveOld!: (v: { session: string; script: string }) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
    const first = start()
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('plot.py')
    let rejectNew!: (e: Error) => void
    vi.mocked(startConvert).mockReturnValueOnce(new Promise((_r, rej) => (rejectNew = rej)))
    const second = start()
    await vi.advanceTimersByTimeAsync(0)
    resolveOld({ session: 'same', script: 'plot.py' })
    await first
    rejectNew(new Error('network down'))
    await second
    expect(useConvertStore.getState().entries['plot.py']?.phase).toBe('failed')
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 'same', script: 'plot.py' })
    const third = start()
    await vi.advanceTimersByTimeAsync(0)
    expect(ridOf(2)).toBe(ridOf(0))
    expect(ridOf(1)).toBe(ridOf(0))
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await third
  })

  it('POST 已受理后 fetchConvert 临时失败：点重试不再 POST，续查同一会话', async () => {
    vi.mocked(startConvert).mockResolvedValueOnce({ session: 's1', script: 'plot.py' })
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'done', final: true, task: { outcome: 'completed' } } as never)
    vi.mocked(fetchConvert).mockReset().mockRejectedValueOnce(new Error('network down'))
    const first = start()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await first
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'failed', session: 's1' })
    expect(startConvert).toHaveBeenCalledTimes(1)

    vi.mocked(fetchConvert).mockResolvedValueOnce({ state: 'ready' } as never)
    const retry = start()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await retry
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(fetchConvert).toHaveBeenCalledTimes(2)
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'ready', session: 's1' })
  })
})


describe('convertStore：R / MATLAB 来源（ADR 0143 修订）', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 's1', script: 'fig.R' })
    vi.mocked(fetchConvert).mockReset()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue({ status: 'running', final: false } as never)
  })
  afterEach(() => {
    useConvertStore.getState().clear('fig.R')
    vi.useRealTimers()
  })

  it('请求体带 source_language、不带 library；Python 来源照旧带 library、不带 source_language', async () => {
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(0)
    const body = vi.mocked(startConvert).mock.calls[0]![0]
    expect(body).toMatchObject({ script: 'fig.R', source_language: 'r', path: null })
    expect(body).not.toHaveProperty('library')
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ sourceLanguage: 'r', library: null, detail: null })
    useConvertStore.getState().clear('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p

    vi.mocked(startConvert).mockResolvedValueOnce({ session: 's2', script: 'plot.py' })
    const q = useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
    await vi.advanceTimersByTimeAsync(0)
    const py = vi.mocked(startConvert).mock.calls[1]![0]
    expect(py).toMatchObject({ script: 'plot.py', library: 'pillow', path: 'out.png' })
    expect(py).not.toHaveProperty('source_language')
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await q
  })

  it('needs_input：落成 failed + reason=needs_input + 原因；不留可续查的会话（再试是同样的结论）', async () => {
    vi.mocked(fetchAiSession).mockResolvedValue({
      status: 'done', final: true,
      task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'needs_input', detail: '某函数换不了' }] },
    } as never)
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'failed', reason: 'needs_input', detail: '某函数换不了' })
    expect(fetchConvert).not.toHaveBeenCalled()
  })

  it('needs_input 没给原因：detail 为 null', async () => {
    vi.mocked(fetchAiSession).mockResolvedValue({
      status: 'done', final: true, task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'needs_input' }] },
    } as never)
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ reason: 'needs_input', detail: null })
  })
})


describe('convertStore：缺依赖卡（Codex #1330 P1-2，候选留着、接着走同一个）', () => {
  const card = {
    kind: 'ask_once' as const, packages: ['pandas'], outside_list: [] as string[],
    env: { kind: 'conda' as const, name: 'myenv' }, action: 'install_managed' as const, impact_digest: 'dg-1',
    target: 'tavotto_managed', creates_environment: false, downloads_python: false, modifies_user_environment: false,
  }
  const held = {
    status: 'done', final: true,
    task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: card }] },
  } as never
  const begin = async () => {
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  }
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 's1', script: 'fig.R' })
    vi.mocked(fetchConvert).mockReset()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue(held)
    vi.mocked(discardConvert).mockReset().mockResolvedValue({ ok: true })
    vi.mocked(resolveConvertDependencies).mockReset().mockResolvedValue({ session: 's1', state: 'resuming' })
    vi.mocked(aiCancel).mockClear()
  })
  afterEach(() => {
    useConvertStore.getState().clear('fig.R')
    vi.useRealTimers()
  })

  it('dependency_decision 的 needs_input：落成 deps + 卡片事实，保留会话（不是 failed）', async () => {
    await begin()
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'deps', session: 's1', card })
  })

  it('主按钮：原样回显卡片摘要给后端，resuming → ready；不再 POST /api/ai/convert（不重跑 Agent）', async () => {
    await begin()
    vi.mocked(fetchConvert).mockResolvedValueOnce({ state: 'resuming' } as never).mockResolvedValue({ state: 'ready', installed: ['pandas'] } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    expect(resolveConvertDependencies).toHaveBeenCalledWith('s1', 'dg-1', 'A')
    expect(useConvertStore.getState().entries['fig.R']?.phase).toBe('resuming')
    await vi.advanceTimersByTimeAsync(POLL_MS * 2 + 20)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'ready', card: null })
    expect(startConvert).toHaveBeenCalledTimes(1)
  })

  it('system 卡片没有「装」动作：resolveDeps 什么都不发', async () => {
    vi.mocked(fetchAiSession).mockResolvedValue({
      status: 'done', final: true,
      task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: { ...card, kind: 'system', action: 'choose_env' } }] },
    } as never)
    await begin()
    await useConvertStore.getState().resolveDeps('fig.R')
    expect(resolveConvertDependencies).not.toHaveBeenCalled()
    expect(useConvertStore.getState().entries['fig.R']?.phase).toBe('deps')
  })

  it('装依赖期间停止：走 discard（后端据此取消正在进行的安装），不走已终局的任务取消；之后轮询的 404 不写回条目', async () => {
    await begin()
    vi.mocked(fetchConvert).mockRejectedValue(new ApiError('convert_not_found', 404, {}))
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('fig.R')
    expect(discardConvert).toHaveBeenCalledWith('s1', 'A')
    expect(aiCancel).not.toHaveBeenCalled()
    // 等后端回话才清卡：discard 回来时条目还在「正在停止…」
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'resuming', stopping: true })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
  })

  it('续跑中停止来得太晚：先「正在停止」，后端对账得 deps_installed → 出「继续」卡（installed），不被清掉', async () => {
    await begin()
    vi.mocked(discardConvert).mockResolvedValue({ ok: true })
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('fig.R')
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'resuming', stopping: true })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'installed', stopping: false })
  })

  it('续跑中停止、后端报候选已删（discarded）：这才清卡', async () => {
    await begin()
    vi.mocked(discardConvert).mockResolvedValue({ ok: true })
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'discarded' } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('fig.R')
    expect(useConvertStore.getState().entries['fig.R']?.stopping).toBe(true)
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toBeUndefined()
  })

  it('续跑中停止、discard 请求本身失败：先对账（GET 转换状态）再按结果展示——对账得 deps_installed 就出「继续」卡', async () => {
    await begin()
    vi.mocked(discardConvert).mockRejectedValue(new Error('network down'))
    vi.mocked(fetchConvert).mockResolvedValueOnce({ state: 'resuming' } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    vi.mocked(fetchConvert).mockReset().mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    await useConvertStore.getState().stop('fig.R')
    expect(fetchConvert).toHaveBeenCalled()
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'installed', stopping: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
  })

  it('续跑中停止、discard 失败且对账得仍在续跑：停止没送到，退回可再点停止（stopping 复位）', async () => {
    await begin()
    vi.mocked(discardConvert).mockRejectedValue(new Error('network down'))
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'resuming' } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    await useConvertStore.getState().stop('fig.R')
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'resuming', stopping: false })
    useConvertStore.getState().clear('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
  })

  it('续跑失败：落 failed + 后端给的原因码', async () => {
    await begin()
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'failed', failure: { reason: 'dependency_install_failed' } } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    await p
    expect(useConvertStore.getState().entries['fig.R']).toMatchObject({ phase: 'failed', reason: 'dependency_install_failed' })
  })
})

describe('convertStore：换环境后按新环境重新判定（Codex #1330 P1，不丢候选、不重跑 Agent）', () => {
  const systemCard = {
    kind: 'system' as const, packages: ['pandas'], outside_list: [] as string[],
    env: { kind: 'system' as const, name: 'Python' }, action: 'choose_env' as const, impact_digest: '',
    target: '', creates_environment: false, downloads_python: false, modifies_user_environment: false,
  }
  const held = {
    status: 'done', final: true,
    task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: systemCard }] },
  } as never
  const envOf = (python: string, projectPython = '') =>
    ({ ok: true, python, project: { open: true, python: projectPython, source: projectPython ? 'project_venv' : undefined } }) as never
  const entry = () => useConvertStore.getState().entries['fig.R']
  const begin = async () => {
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  }
  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useUiStore.setState({ engineEnvOpen: false, engineEnvPick: false })
    useEnvStore.setState({ env: envOf('/usr/bin/python3') })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 's1', script: 'fig.R' })
    vi.mocked(fetchConvert).mockReset()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue(held)
    vi.mocked(replanConvertDependencies).mockReset().mockResolvedValue({ session: 's1', state: 'resuming' })
    vi.mocked(resolveConvertDependencies).mockReset()
    await begin()
  })
  afterEach(() => {
    useConvertStore.getState().clear('fig.R')
    vi.useRealTimers()
  })

  it('chooseEnv 打开现有的环境选择器并记下此刻的环境身份', () => {
    useConvertStore.getState().chooseEnv('fig.R')
    expect(useUiStore.getState()).toMatchObject({ engineEnvOpen: true, engineEnvPick: true })
    expect(entry()?.envBase).not.toBeNull()
  })

  it('选完且解释器变了：自动 replan（只带会话，不带摘要）；期间 resuming + replanning；结果换成新卡', async () => {
    useConvertStore.getState().chooseEnv('fig.R')
    const conda = { ...systemCard, kind: 'ask_once' as const, env: { kind: 'conda' as const, name: 'myenv' }, action: 'install_managed' as const, impact_digest: 'dg-2' }
    vi.mocked(fetchConvert).mockResolvedValueOnce({ state: 'resuming' } as never).mockResolvedValue({ state: 'awaiting_dependencies', dependency_card: conda } as never)
    useEnvStore.setState({ env: envOf('/usr/bin/python3', '.venv/bin/python') })
    await vi.advanceTimersByTimeAsync(0)
    expect(replanConvertDependencies).toHaveBeenCalledWith('s1', 'A')
    expect(entry()).toMatchObject({ phase: 'resuming', replanning: true, envBase: null })
    await vi.advanceTimersByTimeAsync(POLL_MS * 2 + 20)
    expect(entry()).toMatchObject({ phase: 'deps', replanning: false, card: conda })
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(resolveConvertDependencies).not.toHaveBeenCalled()
  })

  it('replan 后不再缺包：继续校验同一个候选，直接 ready', async () => {
    useConvertStore.getState().chooseEnv('fig.R')
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'ready', installed: ['pandas'] } as never)
    useEnvStore.setState({ env: envOf('/opt/conda/bin/python') })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    expect(entry()).toMatchObject({ phase: 'ready', card: null, replanning: false })
  })

  it('解释器没变（环境状态刷新但身份相同）：不 replan，保持原卡', async () => {
    useConvertStore.getState().chooseEnv('fig.R')
    useEnvStore.setState({ env: envOf('/usr/bin/python3') })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    expect(replanConvertDependencies).not.toHaveBeenCalled()
    expect(entry()).toMatchObject({ phase: 'deps', card: systemCard })
  })

  it('没点过「选择运行环境」：环境变了也不替用户 replan', async () => {
    useEnvStore.setState({ env: envOf('/opt/conda/bin/python') })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    expect(replanConvertDependencies).not.toHaveBeenCalled()
  })

  it('replan 回来仍是系统卡：换成新卡、不再自动 replan，等用户再选一次', async () => {
    useConvertStore.getState().chooseEnv('fig.R')
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'awaiting_dependencies', dependency_card: systemCard } as never)
    useEnvStore.setState({ env: envOf('/usr/local/bin/python3') })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    expect(entry()).toMatchObject({ phase: 'deps', envBase: null })
    useEnvStore.setState({ env: envOf('/usr/bin/python3') })
    await vi.advanceTimersByTimeAsync(POLL_MS + 20)
    expect(replanConvertDependencies).toHaveBeenCalledTimes(1)
  })
})

describe('convertStore：续跑遇到传输错误先对账（Codex #1330 P1，GET /api/ai/convert 而非已终结的 AI 会话）', () => {
  const card = {
    kind: 'ask_once' as const, packages: ['pandas'], outside_list: [] as string[],
    env: { kind: 'conda' as const, name: 'myenv' }, action: 'install_managed' as const, impact_digest: 'dg-1',
    target: 'tavotto_managed', creates_environment: false, downloads_python: false, modifies_user_environment: false,
  }
  const held = {
    status: 'done', final: true,
    task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_decision', data: card }] },
  } as never
  const entry = () => useConvertStore.getState().entries['fig.R']
  const begin = async () => {
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  }
  const netErr = () => new TypeError('Failed to fetch')
  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset().mockResolvedValue({ session: 's1', script: 'fig.R' })
    vi.mocked(fetchConvert).mockReset()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue(held)
    vi.mocked(resolveConvertDependencies).mockReset().mockResolvedValue({ session: 's1', state: 'resuming' })
    vi.mocked(replanConvertDependencies).mockReset().mockResolvedValue({ session: 's1', state: 'resuming' })
    await begin()
  })
  afterEach(() => {
    useConvertStore.getState().clear('fig.R')
    vi.useRealTimers()
  })

  it('POST 被后端接受但响应丢失：对账得 ready，可采用（不是 failed / 旧的依赖卡）', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'ready', installed: ['pandas'] } as never)
    await useConvertStore.getState().resolveDeps('fig.R')
    expect(entry()).toMatchObject({ phase: 'ready', card: null, lost: null })
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(1)
    expect(fetchAiSession).toHaveBeenCalledTimes(1) // 只有起始那一次，续跑从不再轮询已终结的 AI 会话
  })

  it('POST 响应丢失但后端在续跑：对账得 resuming，接着轮询到 ready，不重发 POST', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert)
      .mockResolvedValueOnce({ state: 'resuming' } as never) // 对账
      .mockResolvedValue({ state: 'ready' } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(0)
    expect(entry()?.phase).toBe('resuming')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(entry()).toMatchObject({ phase: 'ready' })
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(1)
  })

  it('轮询瞬时失败：对账一次后继续，最终 ready', async () => {
    vi.mocked(fetchConvert)
      .mockRejectedValueOnce(netErr()) // 轮询失败
      .mockResolvedValueOnce({ state: 'resuming' } as never) // 马上对账
      .mockResolvedValue({ state: 'ready' } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS * 3 + 30)
    await p
    expect(entry()).toMatchObject({ phase: 'ready', lost: null })
  })

  it('409 convert_not_awaiting_dependencies：按对账结果展示（已 ready），不卡死', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(new ApiError('x', 409, { code: 'convert_not_awaiting_dependencies' }))
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'ready' } as never)
    await useConvertStore.getState().resolveDeps('fig.R')
    expect(entry()).toMatchObject({ phase: 'ready' })
  })

  it('对账也失败：落「暂时连不上」可重试状态；重试先对账，已 ready 就不重发 POST', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert).mockRejectedValue(netErr())
    await useConvertStore.getState().resolveDeps('fig.R')
    expect(entry()).toMatchObject({ phase: 'failed', reason: 'resume_unreachable', lost: { replanning: false, digest: 'dg-1' } })
    // 重试：对账得 ready
    vi.mocked(fetchConvert).mockReset().mockResolvedValue({ state: 'ready' } as never)
    await useConvertStore.getState().start('fig.R', null, null, 'r')
    expect(entry()).toMatchObject({ phase: 'ready', lost: null })
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(1)
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(fetchAiSession).toHaveBeenCalledTimes(1) // 没有回头轮询已终结的 AI 会话
  })

  it('重试对账得「后端已在续跑」：不重发 POST，接着等', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert).mockRejectedValue(netErr())
    await useConvertStore.getState().resolveDeps('fig.R')
    vi.mocked(fetchConvert).mockReset().mockResolvedValueOnce({ state: 'resuming' } as never).mockResolvedValue({ state: 'ready' } as never)
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(entry()).toMatchObject({ phase: 'ready' })
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(1)
  })

  it('重试对账得「后端仍停在同一张卡」（那次点击根本没到）：重发一次 POST', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert).mockRejectedValue(netErr())
    await useConvertStore.getState().resolveDeps('fig.R')
    vi.mocked(fetchConvert)
      .mockReset()
      .mockResolvedValueOnce({ state: 'awaiting_dependencies', dependency_card: card } as never) // 对账
      .mockResolvedValue({ state: 'ready' } as never)
    const p = useConvertStore.getState().start('fig.R', null, null, 'r')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(2)
    expect(entry()).toMatchObject({ phase: 'ready' })
  })

  it('重试对账仍失败：保持「暂时连不上」，可再重试', async () => {
    vi.mocked(resolveConvertDependencies).mockRejectedValueOnce(netErr())
    vi.mocked(fetchConvert).mockRejectedValue(netErr())
    await useConvertStore.getState().resolveDeps('fig.R')
    await useConvertStore.getState().start('fig.R', null, null, 'r')
    expect(entry()).toMatchObject({ phase: 'failed', reason: 'resume_unreachable', lost: { digest: 'dg-1' } })
    expect(resolveConvertDependencies).toHaveBeenCalledTimes(1)
  })

  it('对账得 deps_installed（停止来得太晚）：落 installed；点继续走 replan 入口 → ready', async () => {
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const p = useConvertStore.getState().resolveDeps('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(entry()).toMatchObject({ phase: 'installed', card: null })
    vi.mocked(fetchConvert).mockReset().mockResolvedValue({ state: 'ready' } as never)
    const c = useConvertStore.getState().continueAfterInstall('fig.R')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await c
    expect(replanConvertDependencies).toHaveBeenCalledWith('s1', 'A')
    expect(entry()).toMatchObject({ phase: 'ready' })
  })
})

describe('convertStore：AI 任务阶段点停止先看取消回复（Codex #1330 P1）', () => {
  const readyStatus = { state: 'ready', session: 's1', script: 'plot.py', has_preview: false } as never
  const runningSession = { status: 'running', final: false } as never
  const startRunning = async () => {
    vi.mocked(startConvert).mockResolvedValue({ session: 's1', script: 'plot.py' })
    const p = useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
    await vi.advanceTimersByTimeAsync(0)
    return { p }
  }
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    setCurrentProjectId('A')
    useConvertStore.setState({ entries: {} })
    useAiStore.setState({ caps: { agents: [] } as never })
    vi.mocked(startConvert).mockReset()
    vi.mocked(aiCancel).mockReset()
    vi.mocked(fetchConvert).mockReset()
    vi.mocked(fetchAiSession).mockReset().mockResolvedValue(runningSession)
  })
  afterEach(() => {
    useConvertStore.getState().clear('plot.py')
    vi.useRealTimers()
  })

  it('local ok：已在本进程终止；对账得候选已不存在（404）→ 清', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert).mockRejectedValue(new ApiError('convert_not_found', 404, {}))
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(fetchConvert).toHaveBeenCalledWith('s1', 'A')
    expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  })

  it('local ok：对账得 deps_installed（停止晚于安装提交点）→ 不清，出「继续」卡；点继续走 replan 入口', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({
      phase: 'installed',
      session: 's1',
      stopping: false,
    })
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(startConvert).toHaveBeenCalledTimes(1)
    vi.mocked(fetchConvert).mockReset().mockResolvedValue({ state: 'ready' } as never)
    const c = useConvertStore.getState().continueAfterInstall('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await c
    expect(replanConvertDependencies).toHaveBeenCalledWith('s1', 'A')
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'ready' })
  })

  it('local ok：任务线程还没收尾（409 convert_not_ready）→ 保持「正在停止」，随后对账得 deps_installed', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert)
      .mockRejectedValueOnce(new ApiError('convert_not_ready', 409, {}))
      .mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const { p } = await startRunning()
    const stopping = useConvertStore.getState().stop('plot.py')
    await vi.advanceTimersByTimeAsync(0)
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', stopping: true })
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await stopping
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'installed' })
    await p
  })

  it('停止后对账先 5xx 后 deps_installed：重试到「继续」卡，不清', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert)
      .mockRejectedValueOnce(new ApiError('boom', 503, {}))
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const { p } = await startRunning()
    const stopping = useConvertStore.getState().stop('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    await stopping
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'installed', stopping: false })
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  })

  it('停止后对账一直网络错误：不清卡，落「暂时连不上」；重试对账得 404 才清', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert).mockRejectedValue(new Error('network down'))
    const { p } = await startRunning()
    const stopping = useConvertStore.getState().stop('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS * 8)
    await stopping
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({
      phase: 'failed',
      reason: 'resume_unreachable',
      stopping: false,
      lost: { stopped: true },
    })
    // 重试 = 先对账：明确的 404 才清，且不重发任何 POST
    vi.mocked(resolveConvertDependencies).mockClear()
    vi.mocked(replanConvertDependencies).mockClear()
    vi.mocked(fetchConvert).mockReset().mockRejectedValue(new ApiError('convert_not_found', 404, {}))
    await useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
    expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
    expect(startConvert).toHaveBeenCalledTimes(1)
    expect(resolveConvertDependencies).not.toHaveBeenCalled()
    expect(replanConvertDependencies).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  })

  it('停止后对账明确 404：清卡', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'local' } as never)
    vi.mocked(fetchConvert).mockRejectedValue(new ApiError('convert_not_found', 404, {}))
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  })

  it('会话以 needs_input + dependency_installed_late 终局：对账出「继续」卡，不显示成失败', async () => {
    vi.mocked(startConvert).mockResolvedValue({ session: 's1', script: 'plot.py' })
    vi.mocked(fetchAiSession).mockResolvedValue({
      status: 'cancelled',
      final: true,
      task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'dependency_installed_late', data: { installed: ['pandas'] } }] },
    } as never)
    vi.mocked(fetchConvert).mockResolvedValue({ state: 'deps_installed', installed: ['pandas'] } as never)
    const p = useConvertStore.getState().start('plot.py', 'pillow', 'out.png')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'installed', session: 's1' })
  })

  it('refused_committing：保留条目「正在停止」，任务做完后对账为 ready 可采用，不重跑 Agent', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: false, reason: 'refused_committing' } as never)
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', session: 's1', stopping: true })
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'done', final: true, task: { outcome: 'completed' } } as never)
    vi.mocked(fetchConvert).mockResolvedValue(readyStatus)
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'ready', session: 's1', stopping: false, stoppedLate: true })
    expect(startConvert).toHaveBeenCalledTimes(1)
  })

  it('owner 模式：结局未定，条目保留；会话 final 为 cancelled 才清', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'owner' } as never)
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', stopping: true })
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'cancelled', final: true } as never)
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['plot.py']).toBeUndefined()
  })

  it('owner 模式：属主拒绝后任务做完 → ready 可采用', async () => {
    vi.mocked(aiCancel).mockResolvedValue({ ok: true, mode: 'owner' } as never)
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'done', final: true, task: { outcome: 'completed' } } as never)
    vi.mocked(fetchConvert).mockResolvedValue(readyStatus)
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'ready', stoppedLate: true })
  })

  it('取消请求本身失败：不清条目，退回可再点停止', async () => {
    vi.mocked(aiCancel).mockRejectedValue(new Error('network down'))
    const { p } = await startRunning()
    await useConvertStore.getState().stop('plot.py')
    expect(useConvertStore.getState().entries['plot.py']).toMatchObject({ phase: 'running', stopping: false })
    useConvertStore.getState().clear('plot.py')
    await vi.advanceTimersByTimeAsync(POLL_MS + 10)
    await p
  })
})
