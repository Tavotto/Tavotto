/**
 * 面板角标的**优先级**（不是"某一档会不会出现"，那是各自的用例）。
 *
 * 这条链此前一条排序判据都没有：每一档都有用例，而"两档同时成立时该显示
 * 哪一个"从来没有被量过。#193（raster 预览角标）要往同一个位置插一档时才
 * 暴露出来——**两个人各插一档、各自的用例都绿，合并后的顺序谁都没验过**。
 *
 * 规则一句话：**阻塞性的压过信息性的。** native 的两句说的是"现在不能
 * 编辑"，`stale` / runtime-stale 说的是"内容可能不是最新"——后者不妨碍用户
 * 动手，前者妨碍。反过来排的表现是：会话跑着的时候用户看到「脚本已更新」，
 * 以为可以重新渲染，点进去撞一条 409。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return { ...real, fetchRuntimeStatus: vi.fn(), fetchRuntimeAssets: vi.fn() }
})

import type { CapturedFigureDescriptor, NativeSessionInfo, NativeSessionState } from '@/lib/api'
import { PanelView } from '@/canvas/PanelView'
import { renderKeyOf } from '@/store/renderStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const FILE_ID = 'runtime:fig.py#Fig1'

const desc = (): CapturedFigureDescriptor => ({
  asset_id: FILE_ID,
  script: 'fig.py',
  entry: '__main__',
  stem: 'Fig1',
  capture_source: 'pyplot',
  execution_profile: 'native',
  original_artifact: null,
  size_mm: [80, 60],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

const session = (state: NativeSessionState, editable: boolean): NativeSessionInfo => ({
  session_id: 'native-1',
  project_root: '/p',
  interpreter: '/p/.venv/bin/python',
  interpreter_fingerprint: 'fp',
  target_kind: 'script',
  target_display: 'fig.py',
  cwd: '/p',
  arg_count: 0,
  python_version: '3.12.1',
  state,
  barrier_reason: '',
  process_pid: 1,
  stems: ['Fig1'],
  descriptors: [desc()],
  script_error: null,
  terminal_error: null,
  exit_code: null,
  figures_captured: 1,
  started_at: 1,
  last_event_at: 1,
  sequence: 1,
  editable,
})

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: FILE_ID,
    fileKind: 'runtime',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    nativeW: 80,
    nativeH: 60,
    overrides: [],
    name: 'Fig1',
    source: {
      script: 'fig.py',
      entry: '__main__',
      stem: 'Fig1',
      captureSource: 'pyplot',
      fingerprint: 'sha256:x',
      sizeMm: [80, 60],
    },
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

const render = () => act(() => root.render(<PanelView obj={panel()} />))

beforeEach(() => {
  vi.mocked(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).fetch ?? (() => {}),
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useNativeSessionStore.setState({ sessions: {}, pendingQueue: [] })
  useRuntimeAssetStore.setState({
    byId: {
      [FILE_ID]: {
        status: 'possibly_stale',
        cached: true,
        registered: true,
        profile: 'native',
        checked: true,
      },
    },
  })
  // **`render.stale` 必须真的置上**：它和 native 是这条链上相邻的两档，
  // 而"相邻"正是排序缺陷唯一藏得住的地方。不置的话这条用例量到的是
  // native vs runtimeBadge（隔着一档），换回原排法照样绿——第一版就是
  // 这么漏掉变异 A 的。
  useRenderStore.setState({
    byKey: { [renderKeyOf(panel())]: { stale: true, status: 'idle' } },
    latest: {},
    building: {},
    tracked: {},
  } as never)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useNativeSessionStore.setState({ sessions: {}, pendingQueue: [] })
  useRuntimeAssetStore.getState().clear()
})

const text = () => document.body.textContent ?? ''

describe('面板角标的优先级', () => {
  it('会话跑着 + 脚本已更新 → 显示"停下来才能编辑"，不是"脚本已更新"', () => {
    // 这两档在链上**相邻**。阻塞性的那条要赢：这时告诉用户"脚本已更新"
    // 会把他推向一个必然 409 的动作（重新渲染）。
    useNativeSessionStore.setState({ sessions: { 'native-1': session('running_script', false) } })
    render()
    expect(text()).toContain('停下来才能编辑')
    expect(text()).not.toContain('脚本已更新')
  })

  it('会话结束 + 脚本已更新 → 显示"会话已结束"', () => {
    // offline 同样是**阻塞性**的：这张图出自一条已经结束的会话，对象级编辑
    // 与权威导出都要重新跑原命令才回得来（`enginesession.resolve` 会拒）。
    // 所以它和 running 用同一条规则，不需要第二套理由。
    useNativeSessionStore.setState({ sessions: {} }) // 没有活会话，profile 仍是 native
    render()
    expect(text()).toContain('会话已结束')
    expect(text()).not.toContain('脚本已更新')
  })

  it('停在屏障上 → native 不出声，让位给"脚本已更新"', () => {
    // 反方向的那一格：此刻编辑一切正常，native 没有话要说，信息性的那条
    // 就该显示出来。少了它，"native 无条件压过一切"也是绿的——而那会把
    // 用户该看到的提示永久盖掉。
    useNativeSessionStore.setState({ sessions: { 'native-1': session('barrier', true) } })
    render()
    expect(text()).not.toContain('停下来才能编辑')
    expect(text()).toContain('脚本已更新')
  })
})
