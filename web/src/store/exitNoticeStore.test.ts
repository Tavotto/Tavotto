import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const desktop = vi.hoisted(() => ({
  onEngineExited: vi.fn(async (_cb: (k: string) => void) => () => {}),
  getEngineExited: vi.fn(async (): Promise<string | null> => null),
  takePreviousExit: vi.fn(async (): Promise<string | null> => null),
  restartEngine: vi.fn(async () => true),
}))
vi.mock('@/lib/desktop', () => desktop)

import { initExitNotices, restartEngineFromCard } from '@/lib/exitNotices'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useExitNoticeStore } from '@/store/exitNoticeStore'

const reset = () => useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null })

beforeEach(() => {
  reset()
  useDiagSendStore.setState({ capability: null, open: false })
  vi.clearAllMocks()
  desktop.onEngineExited.mockImplementation(async () => () => {})
  desktop.getEngineExited.mockImplementation(async () => null)
  desktop.takePreviousExit.mockImplementation(async () => null)
})
afterEach(reset)

describe('exitNoticeStore', () => {
  it('引擎退出登记幂等：事件与回读各来一次不覆盖', () => {
    useExitNoticeStore.getState().setEngineExit('signal')
    useExitNoticeStore.getState().setEngineExit('zero')
    expect(useExitNoticeStore.getState().engineExit).toBe('signal')
  })

  it('下次启动提示：同时最多一张，× 后清空', () => {
    const s = useExitNoticeStore.getState()
    s.showPrevious('app_unclean')
    const first = useExitNoticeStore.getState().previous
    s.showPrevious('engine_exited')
    expect(useExitNoticeStore.getState().previous).toBe(first)
    s.dismissPrevious()
    expect(useExitNoticeStore.getState().previous).toBeNull()
  })
})

describe('initExitNotices', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0))

  it('发送入口开着：取到 app_unclean 才登记；take 恰好一次', async () => {
    useDiagSendStore.setState({ capability: { enabled: true } })
    desktop.takePreviousExit.mockResolvedValueOnce('app_unclean')
    initExitNotices(Promise.resolve())
    await flush()
    expect(desktop.takePreviousExit).toHaveBeenCalledTimes(1)
    expect(useExitNoticeStore.getState().previous?.kind).toBe('app_unclean')
  })

  it('previous = null：不出现', async () => {
    useDiagSendStore.setState({ capability: { enabled: true } })
    initExitNotices(Promise.resolve())
    await flush()
    expect(useExitNoticeStore.getState().previous).toBeNull()
  })

  it('开关 null：不取（取走即清空，白吞一次提示）也不登记', async () => {
    desktop.takePreviousExit.mockResolvedValue('app_unclean')
    initExitNotices(Promise.resolve())
    await flush()
    expect(desktop.takePreviousExit).not.toHaveBeenCalled()
    expect(useExitNoticeStore.getState().previous).toBeNull()
  })

  it('等 load() 完成后才取，而不是在它之前', async () => {
    let done!: () => void
    const loaded = new Promise<void>((r) => (done = r))
    initExitNotices(loaded)
    await flush()
    expect(desktop.takePreviousExit).not.toHaveBeenCalled()
    useDiagSendStore.setState({ capability: { enabled: true } })
    done()
    await flush()
    expect(desktop.takePreviousExit).toHaveBeenCalledTimes(1)
  })

  it('引擎退出：事件与回读都登记，且不看发送开关', async () => {
    let fire!: (k: string) => void
    desktop.onEngineExited.mockImplementationOnce(async (cb) => {
      fire = cb
      return () => {}
    })
    initExitNotices(Promise.resolve())
    await flush()
    fire('nonzero')
    expect(useExitNoticeStore.getState().engineExit).toBe('nonzero')
    reset()
    desktop.getEngineExited.mockResolvedValueOnce('signal')
    initExitNotices(Promise.resolve())
    await flush()
    expect(useExitNoticeStore.getState().engineExit).toBe('signal')
  })

  it('先装监听再回读：监听装好之前引擎退出了，回读仍能登记（修复卡不丢）', async () => {
    let exited: string | null = null
    let install!: (off: () => void) => void
    desktop.onEngineExited.mockImplementationOnce(() => new Promise((r) => (install = r)))
    desktop.getEngineExited.mockImplementation(async () => exited)
    initExitNotices(Promise.resolve())
    await flush()
    // 监听还没装好：此刻不许已经回读（旧顺序在这里就读到了 null）
    expect(desktop.getEngineExited).not.toHaveBeenCalled()
    exited = 'nonzero' // 引擎在「监听装好之前」退出：事件没人接
    install(() => {})
    await flush()
    expect(desktop.getEngineExited).toHaveBeenCalledTimes(1)
    expect(useExitNoticeStore.getState().engineExit).toBe('nonzero')
  })

  it('监听装好之前就卸载：撤销监听、不回读、不登记', async () => {
    let install!: (off: () => void) => void
    const off = vi.fn()
    desktop.onEngineExited.mockImplementationOnce(() => new Promise((r) => (install = r)))
    desktop.getEngineExited.mockImplementation(async () => 'signal')
    const dispose = initExitNotices(Promise.resolve())
    dispose()
    install(off)
    await flush()
    expect(off).toHaveBeenCalledTimes(1)
    expect(desktop.getEngineExited).not.toHaveBeenCalled()
    expect(useExitNoticeStore.getState().engineExit).toBeNull()
  })

  it('卸载后事件监听被取消', async () => {
    const off = vi.fn()
    desktop.onEngineExited.mockImplementationOnce(async () => off)
    const stop = initExitNotices(Promise.resolve())
    await flush()
    stop()
    expect(off).toHaveBeenCalled()
  })
})

describe('restartEngineFromCard', () => {
  it('壳动了手：保持转圈等页面被换掉；没动手：恢复按钮', async () => {
    await restartEngineFromCard()
    expect(useExitNoticeStore.getState().restarting).toBe(true)
    reset()
    desktop.restartEngine.mockResolvedValueOnce(false as never)
    await restartEngineFromCard()
    expect(useExitNoticeStore.getState().restarting).toBe(false)
  })

  it('转圈期间重复点击无效', async () => {
    useExitNoticeStore.setState({ restarting: true })
    await restartEngineFromCard()
    expect(desktop.restartEngine).not.toHaveBeenCalled()
  })
})
