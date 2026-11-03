/**
 * 渲染看门狗是租约（#1207）：距最近一次引擎进度（或请求开始）`watchdogMs` 无动静才断，
 * 总上限 `RENDER_HARD_CAP_MS`。桌面画布（HTTP）与 MCP 内嵌画布（替代传输）共用同一个
 * `ctrl.signal`，所以两条路径各用同一份断言。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions } from '@/lib/api'
import { setEngineTransport } from '@/lib/engineTransport'
import { noteRenderProgress, RENDER_HARD_CAP_MS, useRenderStore } from './renderStore'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const MIN = 60_000
const WINDOW = 5 * MIN // 无 cost → medium 档

/** 永不 settle、只响应 abort 的请求（build 还在跑） */
function hanging(_id: string, _patches: unknown[], opts?: EngineRenderOptions) {
  return new Promise((_resolve, reject) => {
    opts?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })
}

const entryOf = () => Object.values(useRenderStore.getState().byKey)[0]

beforeEach(() => {
  vi.useFakeTimers()
  engineRender.mockReset()
  engineRender.mockImplementation(hanging)
  useRenderStore.getState().clear()
})
afterEach(() => {
  vi.useRealTimers()
  setEngineTransport(null)
})

describe.each([
  ['桌面画布（HTTP）', () => () => {}],
  [
    'MCP 内嵌画布（替代传输）',
    () =>
      setEngineTransport({
        render: (id, patches, opts) => engineRender(id, patches, opts),
        previewPngUrl: async () => '',
        panelSrc: () => null,
      }),
  ],
])('%s', (_name, install) => {
  beforeEach(() => {
    install()
  })

  it('持续有进度：越过一个窗口也不 abort', async () => {
    void useRenderStore.getState().render('Slow.pdf', [])
    for (let i = 0; i < 6; i++) {
      await vi.advanceTimersByTimeAsync(WINDOW - MIN)
      noteRenderProgress('Slow.pdf')
    }
    // 累计 24 分钟，远超 5 分钟窗口
    expect(entryOf()?.status).toBe('rendering')
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(false)
  })

  it('静默超过窗口才断，文案报的是窗口分钟数', async () => {
    void useRenderStore.getState().render('Slow.pdf', [])
    await vi.advanceTimersByTimeAsync(3 * MIN)
    noteRenderProgress('Slow.pdf')
    await vi.advanceTimersByTimeAsync(WINDOW - 1000)
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(true)
    expect(entryOf()?.status).toBe('error')
  })

  it('别的文件的进度不续这个文件的租约', async () => {
    void useRenderStore.getState().render('Slow.pdf', [])
    await vi.advanceTimersByTimeAsync(4 * MIN)
    noteRenderProgress('Other.pdf')
    await vi.advanceTimersByTimeAsync(2 * MIN)
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(true)
  })

  it('总上限仍在：一直有进度也撑不过 BUILD_HARD_TIMEOUT', async () => {
    void useRenderStore.getState().render('Slow.pdf', [])
    let elapsed = 0
    while (elapsed < RENDER_HARD_CAP_MS - MIN) {
      await vi.advanceTimersByTimeAsync(MIN)
      elapsed += MIN
      noteRenderProgress('Slow.pdf')
    }
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(2 * MIN)
    expect(engineRender.mock.calls[0]![2].signal.aborted).toBe(true)
  })
})
