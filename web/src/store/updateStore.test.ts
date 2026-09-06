/**
 * 更新 store 的两条易错处（UI/UX 审计 T48）。
 *
 * 1. **升级失败时用户读到的是什么。** `/api/update/apply` 失败走 500，而后端把
 *    原因放在响应体的 `log` 里、`error` 字段是空的——`jsonFetch` 只认 `error`，
 *    于是 pip 真正说的那句话被扔掉，界面上只剩「HTTP 500」。这一条不是措辞
 *    问题：用户没有任何线索判断该不该再点一次。
 * 2. **「上次检查」那个时刻是哪来的。** 只有**成功**的检查才配写时间戳；失败
 *    时写一个，界面就会拿它去说「那一刻是最新的」，而那一刻其实什么都没查到。
 *
 * 上面那层界面用例（`components/settings/updateStates.test.tsx`）是摆好状态
 * 再看渲染，摆不到这两条**怎么进入那个状态**的路径上。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/desktop', () => ({
  isDesktop: () => true,
  checkDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(),
  relaunchDesktop: vi.fn(),
}))

import { checkDesktopUpdate } from '@/lib/desktop'
import { useUpdateStore } from './updateStore'

const reset = () =>
  useUpdateStore.setState({
    status: null,
    applying: false,
    applyLog: null,
    applyFailed: false,
    restartRequired: false,
    desktopPhase: 'idle',
    desktopUpdate: null,
    desktopError: null,
    desktopChecked: false,
    desktopCheckedAtMs: null,
  })

beforeEach(() => {
  reset()
  vi.unstubAllGlobals()
  vi.mocked(checkDesktopUpdate).mockReset()
})

describe('apply() 的结局', () => {
  const stubApply = (res: Partial<Response> & { json: () => Promise<unknown> }) =>
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(res as Response)))

  it('装成功：不报失败，重启提示跟着后端走', async () => {
    stubApply({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          ok: true,
          command: 'pip install -U tavotto',
          log: 'Successfully installed tavotto-0.14.0',
          restart_required: true,
        }),
    })
    await useUpdateStore.getState().apply()
    const s = useUpdateStore.getState()
    expect(s.applyFailed).toBe(false)
    expect(s.restartRequired).toBe(true)
    expect(s.applyLog).toContain('Successfully installed')
  })

  it('200 但 ok=false（源码检出那条路）也算失败', async () => {
    stubApply({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          ok: false,
          command: 'git pull',
          log: '这是源码检出的运行方式',
          restart_required: false,
        }),
    })
    await useUpdateStore.getState().apply()
    expect(useUpdateStore.getState().applyFailed).toBe(true)
  })

  it('500：把响应体里的 log 取出来，不是「HTTP 500」', async () => {
    stubApply({
      ok: false,
      status: 500,
      json: () =>
        Promise.resolve({
          ok: false,
          command: 'pip install -U tavotto',
          log: 'ERROR: Could not install packages due to an OSError',
          restart_required: false,
        }),
    })
    await useUpdateStore.getState().apply()
    const s = useUpdateStore.getState()
    expect(s.applyFailed).toBe(true)
    expect(s.applyLog).toBe('ERROR: Could not install packages due to an OSError')
    expect(s.applyLog).not.toContain('HTTP 500')
  })

  it('连不上后端（fetch 就抛）：仍然算失败，并留下一句话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('Failed to fetch'))),
    )
    await useUpdateStore.getState().apply()
    const s = useUpdateStore.getState()
    expect(s.applyFailed).toBe(true)
    expect(s.applyLog).toBeTruthy()
  })
})

describe('桌面检查的时刻', () => {
  it('查成功才记时刻', async () => {
    vi.mocked(checkDesktopUpdate).mockResolvedValue(null)
    await useUpdateStore.getState().checkDesktop()
    const s = useUpdateStore.getState()
    expect(s.desktopChecked).toBe(true)
    expect(typeof s.desktopCheckedAtMs).toBe('number')
  })

  it('查失败不记时刻——否则界面会拿它说「那一刻是最新的」', async () => {
    vi.mocked(checkDesktopUpdate).mockRejectedValue(new Error('更新服务不可达'))
    await useUpdateStore.getState().checkDesktop()
    const s = useUpdateStore.getState()
    expect(s.desktopError).toBeTruthy()
    expect(s.desktopCheckedAtMs).toBeNull()
    expect(s.desktopChecked).toBe(false)
  })
})
