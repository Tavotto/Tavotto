/**
 * 会话卡片的动作按钮（ADR 0021 §9.3）。
 *
 * `NativeSession` 的每一条命令都走 `_require_barrier()`——continue / detach /
 * terminate 三个都是。所以**非屏障状态下渲染它们，等于渲染三个必然失败的
 * 按钮**：点下去只会拿到 `native_session_not_at_barrier`，而那句话描述的是
 * 正常状态、不是故障。
 *
 * 与「作废的 descriptor 不留一个点了也没用的运行并连接」是同一个形状。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { NativeSessionInfo, NativeSessionState } from '@/lib/api'
import { NativeSessionCards } from '@/components/NativeSessionCards'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useUiStore } from '@/store/uiStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  cancelNativeSession: vi.fn(),
}))
import { ApiError, cancelNativeSession } from '@/lib/api'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const session = (over: Partial<NativeSessionInfo> = {}): NativeSessionInfo => ({
  session_id: 'native-1',
  project_root: '/p/figures',
  interpreter: '/p/.venv/bin/python',
  interpreter_fingerprint: 'fp',
  target_kind: 'script',
  target_display: 'fig.py',
  cwd: '/p',
  arg_count: 0,
  python_version: '3.12.1',
  state: 'running_script' as NativeSessionState,
  barrier_reason: '',
  process_pid: 42,
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
  ...over,
})

let root: Root
let host: HTMLDivElement

const render = () => act(() => root.render(<NativeSessionCards />))
const put = (one: NativeSessionInfo) =>
  useNativeSessionStore.setState({ sessions: { [one.session_id]: one } })
const reset = () =>
  useNativeSessionStore.setState({
    epoch: 0,
    pendingQueue: [],
    sessions: {},
    busy: {},
    errors: {},
    conflicts: {},
    builtSeq: {},
  })

beforeEach(() => {
  vi.restoreAllMocks()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  reset()
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  reset()
})

const labels = () =>
  Array.from(document.body.querySelectorAll('button')).map((b) => b.textContent ?? '')
const has = (needle: string) => labels().some((t) => t.includes(needle))

describe('NativeSessionCards 的动作按钮', () => {
  const RUNNING: NativeSessionState[] = [
    'starting_python',
    'running_script',
    'waiting_for_figure',
    'continuing',
  ]

  it.each(RUNNING)('%s：三个动作一个都不渲染（点下去必然失败）', (state) => {
    put(session({ state, editable: false }))
    render()
    expect(has('继续运行脚本')).toBe(false)
    expect(has('脱离接管'), '「脱离接管」也走 _require_barrier，非屏障处必然失败').toBe(false)
    expect(has('终止脚本')).toBe(false)
  })

  it('屏障处三个都在——那才是它们能用的时刻', () => {
    put(session({ state: 'barrier', editable: true }))
    render()
    expect(has('继续运行脚本')).toBe(true)
    expect(has('脱离接管')).toBe(true)
    expect(has('终止脚本')).toBe(true)
  })

  it('状态那一行照常说话——按钮没了不等于用户不知道发生了什么', () => {
    put(session({ state: 'running_script' }))
    render()
    expect(document.body.textContent).toContain('脚本正在运行')
    expect(document.body.textContent).toContain('fig.py')
  })

  it('终态只留一个「关闭」，没有动作按钮', () => {
    put(session({ state: 'ended', exit_code: 0, editable: false }))
    render()
    expect(has('继续运行脚本')).toBe(false)
    expect(has('脱离接管')).toBe(false)
    expect(document.body.textContent).toContain('已结束')
  })
})

// 准备条目只需要「当前尝试的计划号」这一个事实（与 `PreparationCard.useStopRun` 同一把尺子）
const prepEntry = (key: string, attempt: string) => ({ key, report: { provider: { attempt_id: attempt } } }) as never
/** 准备界面此刻可见的那张卡 / 角标代表 `fig.py` 这个条目（聚焦 + 展开或角标） */
const putPrepSurface = (attempt: string, mode: 'card' | 'pill' | 'closed' = 'card') => {
  useProjectPreparationStore.setState({ entries: { 'script:fig.py': prepEntry('script:fig.py', attempt) }, focus: 'script:fig.py' })
  useUiStore.setState({ guideCard: mode })
}

describe('桌面拥有的会话不出「tavotto run」式卡（V2 PR4 第二轮，D1）', () => {
  beforeEach(() => {
    useProjectPreparationStore.setState({ entries: {}, focus: null })
    useUiStore.setState({ guideCard: 'closed' })
    vi.mocked(cancelNativeSession).mockReset()
  })

  it('owner=desktop 且存在对应的准备卡（launch_id 对得上）：一张 native 卡都没有，哪个状态都一样', async () => {
    putPrepSurface('plan-1')
    for (const state of ['running_script', 'barrier', 'ended', 'failed'] as NativeSessionState[]) {
      put(session({ owner: 'desktop', launch_id: 'plan-1', state, editable: state === 'barrier' }))
      await render()
      expect(document.body.querySelector('[data-native-session-cards]')).toBeNull()
    }
  })

  // Codex #1044 r4280516790：刷新 / A→B→A 之后准备 store 是空的，仍在跑的桌面会话不能既没卡也没停止入口
  it('准备 store 里没有对应条目（刷新后）：仍在跑的桌面会话照常出卡，并给「停止运行」；点它走取消端点', async () => {
    put(session({ owner: 'desktop', launch_id: 'plan-1', state: 'running_script' }))
    vi.mocked(cancelNativeSession).mockResolvedValue({ confirmed: true, session: session({ owner: 'desktop', state: 'ended' }) } as never)
    await render()
    const stop = document.body.querySelector('[data-native-stop-owned]') as HTMLButtonElement
    expect(stop?.textContent).toBe('停止运行')
    expect(document.body.textContent).not.toContain('放手')
    await act(async () => stop.click())
    expect(cancelNativeSession).toHaveBeenCalledWith('native-1')
  })

  // Codex #1044 r4280601857：准备界面只有一个焦点，隐藏判据看的是「此刻可见的面」，不是「有没有条目」
  it('A 在跑时打开了 B 的卡（A 的条目还在 entries 里、但可见的面是 B）：A 的 native 卡可见且能停', async () => {
    useProjectPreparationStore.setState({
      entries: { 'script:a.py': prepEntry('script:a.py', 'plan-A'), 'script:b.py': prepEntry('script:b.py', 'plan-B') },
      focus: 'script:b.py',
    })
    useUiStore.setState({ guideCard: 'card' })
    put(session({ owner: 'desktop', launch_id: 'plan-A', state: 'running_script' }))
    await render()
    expect(document.body.querySelector('[data-native-stop-owned]')).not.toBeNull()
  })

  it('A 本身在前台：card / pill 都隐藏；收起（closed，没有可见的面）则显示', async () => {
    put(session({ owner: 'desktop', launch_id: 'plan-1', state: 'running_script' }))
    for (const [mode, shown] of [['card', false], ['pill', false], ['closed', true]] as const) {
      putPrepSurface('plan-1', mode)
      await render()
      expect(!!document.body.querySelector('[data-native-stop-owned]'), mode).toBe(shown)
    }
  })

  it('准备条目存在但计划号对不上（它的停止路径也找不到这个会话）：同样出卡', async () => {
    putPrepSurface('plan-OTHER')
    put(session({ owner: 'desktop', launch_id: 'plan-1', state: 'running_script' }))
    await render()
    expect(document.body.querySelector('[data-native-stop-owned]')).not.toBeNull()
  })

  it('停止被拒：卡上如实显示错误，按钮恢复可再点', async () => {
    put(session({ owner: 'desktop', launch_id: 'plan-1', state: 'running_script' }))
    vi.mocked(cancelNativeSession).mockRejectedValue(new ApiError('x', 409, { code: 'native_session_not_found' }))
    await render()
    await act(async () => (document.body.querySelector('[data-native-stop-owned]') as HTMLButtonElement).click())
    expect(document.body.querySelector('[role="alert"]')).not.toBeNull()
    expect((document.body.querySelector('[data-native-stop-owned]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('已结束的桌面会话没有准备卡也不出卡（不堆历史）', async () => {
    put(session({ owner: 'desktop', launch_id: 'plan-1', state: 'ended' }))
    await render()
    expect(document.body.querySelector('[data-native-session-cards]')).toBeNull()
  })

  it('终端拥有的照旧显示；两种并存时只显示终端那条', async () => {
    useNativeSessionStore.setState({
      sessions: {
        a: session({ session_id: 'a', owner: 'desktop', launch_id: 'plan-1' }),
        b: session({ session_id: 'b', owner: 'terminal' }),
        c: session({ session_id: 'c' }), // 老后端没有 owner：按终端算
      },
    })
    putPrepSurface('plan-1')
    await render()
    expect(document.body.querySelectorAll('[data-native-session-cards] > *')).toHaveLength(2)
  })
})
