/**
 * `tavotto run` 的确认屏（ADR 0021 §7）。
 *
 * 这一屏的判据几乎全是**否定式**的，因为它是闸不是提示：CLI 正阻塞着，
 * 用户的 Python 一行都还没跑。
 *
 *  - 展示的是 descriptor 里那条 invocation（解释器 / cwd / 目标 / 图库）；
 *  - `□ 记住此项目和此 Python` **默认不勾**；
 *  - 点外面 / Esc **不算回答**——随手关掉的表现是那个终端一直挂到 attach 超时；
 *  - 参数**只报数量**，值不经过界面。
 *
 * 断言落在**后端调用**上而不是 store 的方法上：用户点的那一下最终要变成
 * 一条带 `remember` 的请求，中间隔着几层 store 不改变这件事，而对着 store
 * 打桩会让"按钮接错了 action"这类缺陷照样绿。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (orig) => {
  const real = await orig<typeof import('@/lib/api')>()
  return {
    ...real,
    fetchNativePending: vi.fn(),
    approveNativePending: vi.fn(),
    cancelNativePending: vi.fn(),
    fetchNativeSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    buildNativeSession: vi.fn(),
  }
})

import {
  approveNativePending,
  cancelNativePending,
  type NativePending,
} from '@/lib/api'
import { NativeConfirmDialog } from '@/components/NativeConfirmDialog'
import { useNativeSessionStore } from '@/store/nativeSessionStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockApprove = vi.mocked(approveNativePending)
const mockCancel = vi.mocked(cancelNativePending)

const ID = '0123456789abcdef0123456789abcdef'

const pending = (over: Partial<NativePending> = {}): NativePending => ({
  native_id: ID,
  created_at: 1,
  expires_at: 999,
  project_root: '/p/figures',
  interpreter: '/p/.venv/bin/python3.12',
  cwd: '/p/work',
  target_kind: 'script',
  target_display: 'kinetics.py',
  arg_count: 0,
  command_fingerprint: 'sha256:c',
  permission_key: 'key',
  python_version: '3.12.1',
  remembered: false,
  ...over,
})

let root: Root
let host: HTMLDivElement

const render = () => act(() => root.render(<NativeConfirmDialog />))

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

const queue = (over: Partial<NativePending> = {}) =>
  useNativeSessionStore.setState({
    pendingQueue: [
      { native_id: ID, info: pending(over), loading: false, submitting: false, error: null },
    ],
  })

beforeEach(() => {
  vi.clearAllMocks()
  mockApprove.mockResolvedValue({ session: {} as never })
  mockCancel.mockResolvedValue({ cancelled: true })
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

const text = () => document.body.textContent ?? ''
const buttonWith = (needle: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(needle),
  )
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}

describe('NativeConfirmDialog', () => {
  it('没有待确认的交接时什么都不渲染', () => {
    render()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  })

  it('展示的是 descriptor 里那条 invocation', () => {
    queue()
    render()
    const shown = text()
    expect(shown).toContain('kinetics.py')
    expect(shown).toContain('/p/.venv/bin/python3.12')
    expect(shown).toContain('/p/work')
    expect(shown).toContain('/p/figures')
    expect(shown).toContain('3.12.1')
  })

  it('权限说明四句话都在——这是用户唯一一次被告知权限边界的地方', () => {
    queue()
    render()
    const shown = text()
    expect(shown).toContain('项目自己的 Python')
    expect(shown).toContain('相同的文件权限')
    expect(shown).toContain('只接管当前 Python 进程中的 Matplotlib Figure')
    expect(shown).toContain('仅运行你信任的代码')
  })

  it('「记住此项目和此 Python」默认不勾，不勾就 remember=false', async () => {
    queue()
    render()
    const box = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(box, '缺少「记住」勾选框').toBeTruthy()
    expect(box.checked).toBe(false)

    await click(buttonWith('运行并连接')!)
    expect(mockApprove).toHaveBeenCalledWith(ID, false)
  })

  it('勾上之后送出去的才是 remember=true', async () => {
    queue()
    render()
    const box = document.body.querySelector('input[type="checkbox"]') as HTMLInputElement
    await act(async () => {
      box.click()
    })
    await click(buttonWith('运行并连接')!)
    expect(mockApprove).toHaveBeenCalledWith(ID, true)
  })

  it('参数**只报数量**：值不经过界面', () => {
    queue({ arg_count: 3 })
    render()
    expect(text()).toContain('3')
    // descriptor 里根本没有"值"这一项，界面自然也说不出来。这条钉住的是
    // 「以后有人为了更友好把 argv 塞进 descriptor」的那一天。
    expect(text()).not.toContain('--')
  })

  it('Esc **不算回答**：闸不会因为一次误触就放行或取消', async () => {
    queue()
    render()
    await act(async () => {
      document.body.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      )
    })
    expect(mockApprove).not.toHaveBeenCalled()
    expect(mockCancel).not.toHaveBeenCalled()
    expect(useNativeSessionStore.getState().pendingQueue).toHaveLength(1)
  })

  it('「取消」真的去取消，不是把对话框藏起来', async () => {
    queue()
    render()
    await click(buttonWith('取消')!)
    expect(mockCancel).toHaveBeenCalledWith(ID)
    expect(useNativeSessionStore.getState().pendingQueue).toHaveLength(0)
  })

  it('取不到 descriptor 时给的是原因 + 一个关得掉的出口', async () => {
    useNativeSessionStore.setState({
      pendingQueue: [
        {
          native_id: ID,
          info: null,
          loading: false,
          submitting: false,
          error: { code: 'native_handoff_expired', message: '后端原文' },
        },
      ],
    })
    render()
    // 按 code 翻出来的那句，不是后端原文
    expect(text()).toContain('已过期')
    expect(text()).not.toContain('后端原文')
    const close = buttonWith('关闭')
    expect(close, '缺少能关掉它的出口').toBeTruthy()
    await click(close!)
    expect(useNativeSessionStore.getState().pendingQueue).toHaveLength(0)
  })

  it('descriptor 作废之后不留一个点了也没用的「运行并连接」', async () => {
    // 后端在 attach **之前**就 consume() 了 descriptor（issue #190），所以
    // attach 被拒时凭据已经成了墓碑——再点一次只会拿到同一条错误。
    useNativeSessionStore.setState({
      pendingQueue: [
        {
          native_id: ID,
          info: pending(),
          loading: false,
          submitting: false,
          error: { code: 'native_handoff_consumed', message: '后端原文' },
        },
      ],
    })
    render()
    expect(text()).toContain('已经被处理过')
    expect(buttonWith('运行并连接'), '作废之后还留着一个点不动的按钮').toBeUndefined()
    const close = buttonWith('关闭')
    expect(close, '缺少能关掉它的出口').toBeTruthy()
    await click(close!)
    expect(useNativeSessionStore.getState().pendingQueue).toHaveLength(0)
  })

  it('可以重试的失败（环境被占）**保留**「运行并连接」', async () => {
    // 反方向：不是所有批准失败都意味着 descriptor 没了。装依赖占着环境时
    // 等一会儿再点就能成——这一格要是也被收掉，用户就只能重敲一遍命令。
    useNativeSessionStore.setState({
      pendingQueue: [
        {
          native_id: ID,
          info: pending(),
          loading: false,
          submitting: false,
          error: { code: 'environment_mutating', message: '后端原文' },
        },
      ],
    })
    render()
    expect(buttonWith('运行并连接'), '可重试的失败不该把按钮收掉').toBeTruthy()
  })

  it('排队中的第二条会说出来，不是悄悄压着', () => {
    useNativeSessionStore.setState({
      pendingQueue: [
        { native_id: ID, info: pending(), loading: false, submitting: false, error: null },
        {
          native_id: 'ffffffffffffffffffffffffffffffff',
          info: pending(),
          loading: false,
          submitting: false,
          error: null,
        },
      ],
    })
    render()
    expect(text()).toContain('排队')
  })
})
