/**
 * 调度器的边界与两条新 API。行为本身（防抖合并 / 降质只给含图像面板 / `none` 占位 /
 * flush 定稿 / 布尔参数兼容）已由 `hooks/useEngineSync.test.ts` 逐条钉着——搬家时那些
 * 用例改的只是 import 路径，一条判据没动。这里只补搬家本身带来的两件事：
 *
 * 1. **依赖方向**：调度器不许 import `store/actions`——否则 actions ↔ useEngineSync 那个
 *    两节点环只是换成了 actions → scheduler → actions 的三节点环。判源码而不判行为：
 *    环在运行时没有任何症状。
 * 2. `cancelScheduledRender`：取消挂起的防抖渲染，但**不动** `wantPatches` 占位（那是
 *    同步器的跳过判据；清了它同步器会立刻替这次改动再发一次）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import type { PanelChartState } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import { renderRequestIdentity } from './renderStore'
import { cancelScheduledRender, hasScheduledRender, requestRender } from './renderScheduler'

const schedulerSource = Object.values(
  import.meta.glob('/src/store/renderScheduler.ts', { query: '?raw', import: 'default', eager: true }),
)[0] as string

describe('chart调度仍使用唯一renderStore', () => {
  const send = vi.fn<ReturnType<typeof useRenderStore.getState>['render']>(async () => {})
  beforeEach(() => {
    vi.useFakeTimers()
    send.mockClear()
    useRenderStore.getState().clear()
    useRenderStore.setState({ render: send })
  })
  afterEach(() => { vi.useRealTimers() })

  function chartPanel() {
    const v = chartVectors.vectors.find(v => v.expect === 'ok')!
    const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
    return { ...panel('chart', 'chart.svg'), script: null, fileKind: 'svg' as const, chart }
  }

  it('普通前台调用仍只有原来六个实参', () => {
    requestRender(panel('plain', 'plot.pdf'), 'immediate')
    expect(send.mock.calls[0]).toHaveLength(6)
  })

  it('chart none完整占位；取消/防抖保留同一完整chart身份', () => {
    const p = chartPanel()
    requestRender(p, 'none')
    expect(send).not.toHaveBeenCalled()
    expect(useRenderStore.getState().get(renderKeyOf(p)).wantPatches).toBe(renderRequestIdentity(p.overrides, p.chart))
    requestRender(p, 'defer')
    cancelScheduledRender(p.id)
    vi.advanceTimersByTime(1000)
    expect(send).not.toHaveBeenCalled()
  })

  it('防抖定时器携带排期时完整chart快照而非后续可变引用', () => {
    const p = chartPanel(), original = structuredClone(p)
    requestRender(p, 'defer')
    p.chart.model.axes[0].xlim[1] += 10
    vi.advanceTimersByTime(300)
    expect(send).toHaveBeenCalledOnce()
    expect(send.mock.calls[0][0]).toBe('chart.svg')
    expect(send.mock.calls[0][1]).toEqual(original.overrides)
    expect(send.mock.calls[0][7]).toEqual(original.chart)
  })

  it('来源附件参与none占位并在防抖时深拷贝，第九参只在ref存在时发送', () => {
    const p: PanelObject = chartPanel()
    p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: p.chart!.model.scene_digest }
    const original = structuredClone(p)
    requestRender(p, 'none')
    expect(useRenderStore.getState().get(renderKeyOf(p)).wantPatches)
      .toBe(renderRequestIdentity(p.overrides, p.chart, p.chartSource))
    requestRender(p, 'defer')
    p.chartSource.bytesSha256 = 'b'.repeat(64)
    vi.advanceTimersByTime(300)
    expect(send.mock.calls[0]).toHaveLength(9)
    expect(send.mock.calls[0][8]).toEqual(original.chartSource)
    expect(send.mock.calls[0][8]).not.toBe(p.chartSource)
  })
})

const hookSource = Object.values(
  import.meta.glob('/src/hooks/useEngineSync.ts', { query: '?raw', import: 'default', eager: true }),
)[0] as string

describe('依赖方向', () => {
  it('读到了两份源码（判据的前提）', () => {
    expect(schedulerSource).toContain('export function requestRender')
    expect(hookSource).toContain('export function useEngineSync')
  })

  it('调度器不 import store/actions（否则两节点环换成三节点环）', () => {
    expect(schedulerSource).not.toMatch(/from\s+['"]@\/store\/actions['"]/)
    expect(schedulerSource).not.toMatch(/from\s+['"]\.\/actions['"]/)
  })

  it('hook 文件不 import store/actions，也不再定义调度函数', () => {
    expect(hookSource).not.toMatch(/from\s+['"]@\/store\/actions['"]/)
    expect(hookSource).not.toMatch(/export function (requestRender|flushRender)\b/)
  })
})

function panel(id: string, fileId: string): PanelObject {
  return {
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: [{ gid: 'g0', prop: 'color', value: '#000' }],
  } as PanelObject
}

describe('cancelScheduledRender', () => {
  let calls = 0
  beforeEach(() => {
    vi.useFakeTimers()
    calls = 0
    useRenderStore.getState().clear()
    useRenderStore.setState({
      render: async () => {
        calls += 1
      },
    })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('取消之后防抖到点也不发；wantPatches 占位原样留着', () => {
    const p = panel('a', 'Fig1.pdf')
    requestRender(p, 'defer')
    expect(hasScheduledRender('a')).toBe(true)
    cancelScheduledRender('a')
    expect(hasScheduledRender('a')).toBe(false)
    vi.advanceTimersByTime(1000)
    expect(calls).toBe(0)
    expect(useRenderStore.getState().get(renderKeyOf(p)).wantPatches).toBe(JSON.stringify(p.overrides))
  })

  it('没挂计时器时取消是 no-op', () => {
    expect(() => cancelScheduledRender('nobody')).not.toThrow()
    expect(hasScheduledRender('nobody')).toBe(false)
  })
})
