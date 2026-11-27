// NEXT_PHASE F4-F6 边界回归（2026-11-05 复核反例）
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chartCompute } from '@/lib/chartApi'
import { revisionOf, useChartRenderStore } from './chartRenderStore'
import { setCurrentProjectId } from '@/lib/session'

vi.mock('@/lib/chartApi', () => ({ chartCompute: vi.fn() }))

function chartState(range: [number, number]) {
  return {
    model: {
      schema: 'tavotto.chart-model/1',
      rev: 1,
      axes: [{ id: 'ax1', xlim: range }],
      series: [{ id: 's1', node: 'curve' }],
      dataset: { columns: {} },
      scene_digest: 'd'.repeat(64),
      data_digest: 'e'.repeat(64),
      engine: { matplotlib: '3.11.2', numpy: '2.1.0', tavotto: '0.20.0' },
    },
    binding: { model_rev: 1, scene_digest: 'd'.repeat(64), entries: [] },
  } as never
}

beforeEach(() => {
  setCurrentProjectId(null)
  vi.mocked(chartCompute).mockReset()
  useChartRenderStore.setState({ byRevision: {}, generation: 0 })
})

it('dropping one pending variant does not strand a different pending variant', async () => {
  let resolveA!: (value: never) => void, resolveB!: (value: never) => void
  vi.mocked(chartCompute).mockReturnValueOnce(new Promise(r => { resolveA = r })).mockReturnValueOnce(new Promise(r => { resolveB = r }))
  const a = chartState([0, 1]), b = chartState([2, 3])
  const store = useChartRenderStore.getState()
  store.ensure('f.svg', a); store.ensure('f.svg', b)
  store.drop(revisionOf(a)!)
  resolveA({ svg: '<svg id="dropped"/>', render_revision: 'A' } as never)
  resolveB({ svg: '<svg id="kept"/>', render_revision: 'B' } as never)
  await Promise.resolve(); await Promise.resolve()
  expect(useChartRenderStore.getState().byRevision[revisionOf(a)!]).toBeUndefined()
  expect(useChartRenderStore.getState().byRevision[revisionOf(b)!].status).toBe('ready')
})

it('actual project changes clear the chart cache', async () => {
  useChartRenderStore.setState({ byRevision: { fixture: { status: 'ready', svg: '<svg/>' } } })
  setCurrentProjectId('B')
  expect(useChartRenderStore.getState().byRevision).toEqual({})
})

describe('chart render identity and lifecycle (F4-F6)', () => {
  it('F4: different axis ranges produce different revisions', () => {
    const rev1 = revisionOf(chartState([0, 1]))
    const rev2 = revisionOf(chartState([2, 6]))
    expect(rev1).not.toBe(rev2)
  })

  it('F5: two variants each trigger their own compute', async () => {
    vi.mocked(chartCompute).mockResolvedValue({
      svg: '<svg/>',
      facts: {},
      scene_digest: 'd'.repeat(64),
      render_revision: 'r',
      model_digest: 'm',
      binding_digest: 'b',
      engine: { matplotlib: '3', numpy: '2', tavotto: '0' },
    })
    const s = useChartRenderStore.getState()
    s.ensure('f.svg', chartState([0, 1]))
    s.ensure('f.svg', chartState([2, 6]))
    await vi.waitFor(() => {
      const st = useChartRenderStore.getState()
      const revisions = Object.keys(st.byRevision)
      expect(revisions.length).toBeGreaterThanOrEqual(2)
    })
    expect(vi.mocked(chartCompute).mock.calls.length).toBe(2)
  })

  it('F6: clear discards in-flight responses', async () => {
    let resolve!: (v: unknown) => void
    vi.mocked(chartCompute).mockReturnValue(
      new Promise((r) => {
        resolve = r
      }) as never,
    )
    const s = useChartRenderStore.getState()
    s.ensure('f.svg', chartState([0, 1]))
    useChartRenderStore.getState().clear()
    resolve({
      svg: '<svg id="stale"/>',
      facts: {},
      scene_digest: 'd'.repeat(64),
      render_revision: 'r',
      model_digest: 'm',
      binding_digest: 'b',
      engine: { matplotlib: '3', numpy: '2', tavotto: '0' },
    })
    await Promise.resolve()
    await Promise.resolve()
    expect(useChartRenderStore.getState().byRevision).toEqual({})
  })
})
