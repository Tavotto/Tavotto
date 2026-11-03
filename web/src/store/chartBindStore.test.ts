// chartBindStore 状态机测试（SG10）：编排正确、判据不做第二份、错误透传。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { PanelObject } from '@/types/document'
import { chartBindConfirmReady, useChartBindStore } from './chartBindStore'

vi.mock('@/lib/chartApi', () => ({
  chartInspect: vi.fn(),
  chartBind: vi.fn(),
  chartCompute: vi.fn(),
}))

const SCENE = {
  scene_digest: 'd'.repeat(64),
  w_pt: 360,
  h_pt: 252,
  view_box: [0, 0, 360, 252],
  nodes: [{ id: 'figure_1', tag: 'g' }, { id: 'curve', tag: 'path' }],
  truncated: false,
}

const CHART = {
  model: {
    schema: 'tavotto.chart-model/1',
    rev: 1,
    axes: [],
    series: [],
    dataset: { columns: {} },
    scene_digest: 'd'.repeat(64),
    data_digest: 'e'.repeat(64),
    engine: { matplotlib: '3.11.2', numpy: '2.1.0', tavotto: '0.20.0' },
  },
  binding: { model_rev: 1, scene_digest: 'd'.repeat(64), entries: [] },
}

function panel(): PanelObject {
  return {
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: 'chart.svg',
    fileKind: 'svg',
    nativeW: 360,
    nativeH: 252,
    overrides: [],
  } as unknown as PanelObject
}

beforeEach(() => {
  vi.mocked(chartInspect).mockReset()
  vi.mocked(chartBind).mockReset()
  vi.mocked(chartCompute).mockReset()
  useChartBindStore.setState({
    open: false,
    panelId: null,
    file: null,
    step: 'scene',
    scene: null,
    form: useChartBindStore.getState().form,
    result: null,
    previewSvg: null,
    error: null,
    busy: false,
  })
})

describe('chartBindStore', () => {
  it('openFor loads the scene and lands on the data step', async () => {
    vi.mocked(chartInspect).mockResolvedValue(SCENE)
    await useChartBindStore.getState().openFor(panel())
    const s = useChartBindStore.getState()
    expect(s.open).toBe(true)
    expect(s.step).toBe('data')
    expect(s.scene?.scene_digest).toBe(SCENE.scene_digest)
    expect(s.form.seriesNode).toBe('curve') // 候选识别只辅助：预填第一个图形节点
    expect(s.form.rect).toEqual([0, 0, 360, 252])
  })

  it('inspect failure surfaces the stable code and stays open', async () => {
    vi.mocked(chartInspect).mockRejectedValue(
      Object.assign(new Error('nope'), { code: 'svg_dtd_forbidden' }),
    )
    await useChartBindStore.getState().openFor(panel())
    const s = useChartBindStore.getState()
    expect(s.error?.code).toBe('svg_dtd_forbidden')
    expect(s.step).toBe('scene')
  })

  it('openFor ignores non-svg panels', async () => {
    const raster = { ...panel(), fileKind: 'raster' } as unknown as PanelObject
    await useChartBindStore.getState().openFor(raster)
    expect(useChartBindStore.getState().open).toBe(false)
    expect(chartInspect).not.toHaveBeenCalled()
  })

  it('confirmBinding posts the form and fetches a preview', async () => {
    vi.mocked(chartBind).mockResolvedValue(CHART)
    vi.mocked(chartCompute).mockResolvedValue({
      svg: '<svg/>',
      facts: {},
      scene_digest: CHART.model.scene_digest,
    })
    const store = useChartBindStore.getState()
    useChartBindStore.setState({ file: 'chart.svg', form: { ...store.form, seriesNode: 'curve', xCol: 'x', yCol: 'y' } })
    await useChartBindStore.getState().confirmBinding()
    const s = useChartBindStore.getState()
    expect(s.step).toBe('confirm')
    expect(s.result).toEqual(CHART)
    expect(s.previewSvg).toBe('<svg/>')
    // 证据按表单透传（判据在后端，这里只看形状）
    const bindCall = vi.mocked(chartBind).mock.calls[0]?.[0]
    expect(bindCall?.x?.scale).toBe('linear')
  })

  it('bind errors keep the wizard open with the code', async () => {
    vi.mocked(chartBind).mockRejectedValue(
      Object.assign(new Error('bad data'), { code: 'bind_evidence_invalid' }),
    )
    useChartBindStore.setState({ file: 'chart.svg' })
    await useChartBindStore.getState().confirmBinding()
    const s = useChartBindStore.getState()
    expect(s.step).not.toBe('confirm')
    expect(s.error?.code).toBe('bind_evidence_invalid')
  })

  it('confirm readiness mirrors required fields without judging data', () => {
    const base = useChartBindStore.getState()
    expect(chartBindConfirmReady({ ...base, file: null })).toBe(false)
    expect(
      chartBindConfirmReady({
        ...base,
        file: 'chart.svg',
        form: { ...base.form, seriesNode: 'curve', xCol: 'x', yCol: 'y' },
      }),
    ).toBe(true)
    expect(
      chartBindConfirmReady({
        ...base,
        file: 'chart.svg',
        form: { ...base.form, kind: 'image', gridText: '' },
      }),
    ).toBe(false)
  })
})
