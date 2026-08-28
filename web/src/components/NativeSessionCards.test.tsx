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
    expect(has('放手'), '「放手」也走 _require_barrier，非屏障处必然失败').toBe(false)
    expect(has('终止脚本')).toBe(false)
  })

  it('屏障处三个都在——那才是它们能用的时刻', () => {
    put(session({ state: 'barrier', editable: true }))
    render()
    expect(has('继续运行脚本')).toBe(true)
    expect(has('放手')).toBe(true)
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
    expect(has('放手')).toBe(false)
    expect(document.body.textContent).toContain('已结束')
  })
})
