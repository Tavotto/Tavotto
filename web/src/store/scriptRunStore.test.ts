import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cancelProbe, probeScript, type CapturedFigureDescriptor, type ProbeResult } from '@/lib/api'
import { useRenderStore } from './renderStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { isBusyPhase, needsNative, useScriptRunStore } from './scriptRunStore'

vi.mock('@/lib/api', () => ({
  // scriptRunStore 直接用的三样
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  ApiError: class ApiError extends Error {
    status: number
    body: Record<string, unknown>
    constructor(message: string, status: number, body: Record<string, unknown>) {
      super(message)
      this.status = status
      this.body = body
    }
  },
  // 成功副作用会触发的相邻 store（本文件只关心状态机，让它们安静成功）
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchRuntimeStatus: vi.fn(),
  engineRender: vi.fn(),
  EngineError: class EngineError extends Error {},
}))

const mockProbe = vi.mocked(probeScript)
const mockCancel = vi.mocked(cancelProbe)

const desc = (stem: string): CapturedFigureDescriptor => ({
  asset_id: `runtime:fig.py#${stem}`,
  script: 'fig.py',
  entry: '__main__',
  stem,
  capture_source: 'pyplot',
  execution_profile: 'safe',
  original_artifact: null,
  size_mm: [100, 80],
  source_fingerprint: 'sha256:x',
  can_writeback_artifact: false,
  can_writeback_source: false,
})

const ok = (descriptors: CapturedFigureDescriptor[], dropped = 0): ProbeResult => ({
  script: 'fig.py',
  entry: '__main__',
  stems: descriptors.map((d) => d.stem),
  descriptors,
  error: null,
  tried: ['__main__'],
  registered: true,
  dropped_figures: dropped,
})

const failed = (code: string): ProbeResult => ({
  script: 'fig.py',
  entry: null,
  stems: [],
  descriptors: [],
  error: { code, message: '原文', params: {} },
  tried: ['__main__'],
  registered: false,
})

/** 手动可控的 probe promise */
function deferredProbe() {
  let resolve!: (r: ProbeResult) => void
  mockProbe.mockImplementationOnce(
    () => new Promise<ProbeResult>((r) => (resolve = r)),
  )
  return { resolve: (r: ProbeResult) => resolve(r) }
}

const flush = () => new Promise((r) => setTimeout(r, 0))
const state = (script = 'fig.py') => useScriptRunStore.getState().byScript[script]

beforeEach(() => {
  useScriptRunStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  mockProbe.mockReset()
  mockCancel.mockClear()
})

describe('scriptRunStore 状态机', () => {
  it('run → captured_one：单张图', async () => {
    mockProbe.mockResolvedValue(ok([desc('fig')]))
    await useScriptRunStore.getState().run('fig.py')
    expect(state().phase).toBe('captured_one')
    expect(state().descriptors).toHaveLength(1)
  })

  it('run → captured_many：多张图**全部**保留（负向反证 #4：只留第一张这里红）', async () => {
    mockProbe.mockResolvedValue(ok([desc('a'), desc('b'), desc('c')], 2))
    await useScriptRunStore.getState().run('fig.py')
    expect(state().phase).toBe('captured_many')
    expect(state().descriptors.map((d) => d.stem)).toEqual(['a', 'b', 'c'])
    expect(state().droppedFigures).toBe(2)
  })

  it('错误码 → 相位映射（missing_dependency / timeout / no_figure / cancelled / failed）', async () => {
    const cases: Array<[string, string]> = [
      ['missing_dependency', 'missing_dependency'],
      ['execution_timeout', 'timeout'],
      ['script_no_figure', 'no_figure'],
      ['execution_cancelled', 'cancelled'],
      ['script_probe_failed', 'failed'],
    ]
    for (const [code, phase] of cases) {
      useScriptRunStore.getState().clear()
      mockProbe.mockResolvedValueOnce(failed(code))
      await useScriptRunStore.getState().run('fig.py')
      expect(state().phase).toBe(phase)
      expect(state().error?.code).toBe(code)
    }
  })

  it('needsNative 判据：缺包 / 超时 / 失败进「可能需要原环境」，取消与没出图不进', async () => {
    for (const [code, expected] of [
      ['missing_dependency', true],
      ['execution_timeout', true],
      ['script_probe_failed', true],
      ['execution_cancelled', false],
      ['script_no_figure', false],
    ] as const) {
      useScriptRunStore.getState().clear()
      mockProbe.mockResolvedValueOnce(failed(code))
      await useScriptRunStore.getState().run('fig.py')
      expect(needsNative(state())).toBe(expected)
    }
  })

  it('同一脚本防并发：busy 期间第二次 run 是 no-op', async () => {
    const d = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    expect(isBusyPhase(state().phase)).toBe(true)
    void useScriptRunStore.getState().run('fig.py')
    expect(mockProbe).toHaveBeenCalledTimes(1)
    d.resolve(ok([desc('fig')]))
    await flush()
    expect(state().phase).toBe('captured_one')
  })

  it('SSE probe.started：starting_runtime → running；终态不受影响', async () => {
    const d = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    expect(state().phase).toBe('starting_runtime')
    useScriptRunStore.getState().markRunning('fig.py')
    expect(state().phase).toBe('running')
    d.resolve(ok([desc('fig')]))
    await flush()
    useScriptRunStore.getState().markRunning('fig.py')
    expect(state().phase).toBe('captured_one')
  })

  it('cancel：打后端取消端点，原请求以 execution_cancelled 落地', async () => {
    const d = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    useScriptRunStore.getState().cancel('fig.py')
    expect(mockCancel).toHaveBeenCalledWith('fig.py')
    expect(state().cancelRequested).toBe(true)
    // 重复点取消不再重复发请求
    useScriptRunStore.getState().cancel('fig.py')
    expect(mockCancel).toHaveBeenCalledTimes(1)
    d.resolve(failed('execution_cancelled'))
    await flush()
    expect(state().phase).toBe('cancelled')
    expect(state().cancelRequested).toBe(false)
  })

  it('cancel 输给成功：脚本在取消前跑完，结果照常保留', async () => {
    const d = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    useScriptRunStore.getState().cancel('fig.py')
    d.resolve(ok([desc('fig')]))
    await flush()
    expect(state().phase).toBe('captured_one')
  })

  it('切项目（clear）后在途响应作废，绝不落进新项目（负向反证 #6）', async () => {
    const d = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    useScriptRunStore.getState().clear() // 切项目
    d.resolve(ok([desc('fig')]))
    await flush()
    expect(state()).toBeUndefined()
  })

  it('迟到响应不能覆盖新请求（代际检查）', async () => {
    const first = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    // 直接把第一次的状态清掉再跑第二次（相当于用户 reset 后再跑）
    useScriptRunStore.getState().clear()
    const second = deferredProbe()
    void useScriptRunStore.getState().run('fig.py')
    first.resolve(ok([desc('stale-answer')]))
    await flush()
    // 第一次的答案不许盖住第二次的 busy 态
    expect(isBusyPhase(state().phase)).toBe(true)
    second.resolve(ok([desc('fresh-answer')]))
    await flush()
    expect(state().descriptors[0].stem).toBe('fresh-answer')
  })

  it('成功后 runtime 面板转入引擎跟踪（显式动作可以热重建）', async () => {
    mockProbe.mockResolvedValue(ok([desc('fig')]))
    const spy = vi.spyOn(useRenderStore.getState(), 'markStale')
    await useScriptRunStore.getState().run('fig.py')
    expect(spy).toHaveBeenCalledWith(['runtime:fig.py#fig'])
    spy.mockRestore()
  })

  it('HTTP 层失败（409 probe_in_progress 等）按 code 落相位', async () => {
    const { ApiError } = await import('@/lib/api')
    mockProbe.mockRejectedValue(
      new ApiError('冲突', 409, { code: 'probe_in_progress', params: { script: 'fig.py' } }),
    )
    await useScriptRunStore.getState().run('fig.py')
    expect(state().phase).toBe('failed')
    expect(state().error?.code).toBe('probe_in_progress')
  })
})
