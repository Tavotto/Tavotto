// chartBindStore 状态机测试（SG10）：编排正确、判据不做第二份、错误透传。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { SceneInventory } from '@/lib/chartApi'
import type { PanelChartState, PanelObject } from '@/types/document'
import { chartBindConfirmReady, useChartBindStore } from './chartBindStore'
import { DEFAULT_FORM, DEFAULT_SERIES } from './chartBindStore'
import { setCurrentProjectId } from '@/lib/session'

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

function panel(id = 'p1'): PanelObject {
  return {
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: `${id}.svg`,
    fileKind: 'svg',
    nativeW: 360,
    nativeH: 252,
    overrides: [],
  } as unknown as PanelObject
}

beforeEach(() => {
  setCurrentProjectId(null)
  vi.mocked(chartInspect).mockReset()
  vi.mocked(chartBind).mockReset()
  vi.mocked(chartCompute).mockReset()
  useChartBindStore.setState({
    open: false,
    panelId: null,
    file: null,
    openSeq: 0,
    step: 'scene',
    scene: null,
    form: DEFAULT_FORM,
    formRev: 0,
    result: null,
    previewSvg: null,
    previewOk: false,
    error: null,
    busy: false,
  })
})

it('late compute cannot clear a newer request or restore obsolete preview', async () => {
  const first = deferred<Awaited<ReturnType<typeof chartCompute>>>()
  const second = deferred<Awaited<ReturnType<typeof chartCompute>>>()
  vi.mocked(chartCompute).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
  useChartBindStore.setState({ file: 'chart.svg', result: { ...CHART, formRev: 0 }, formRev: 0 })
  const old = useChartBindStore.getState().runPreview()
  const current = useChartBindStore.getState().runPreview()
  first.resolve({ svg: '<svg id="old"/>', facts: {}, scene_digest: '', render_revision: '', model_digest: '', binding_digest: '', engine: CHART.model.engine })
  await old
  expect(useChartBindStore.getState().busy).toBe(true)
  expect(useChartBindStore.getState().previewSvg).toBeNull()
  second.resolve({ svg: '<svg id="new"/>', facts: {}, scene_digest: '', render_revision: '', model_digest: '', binding_digest: '', engine: CHART.model.engine })
  await current
  expect(useChartBindStore.getState().previewSvg).toContain('new')
})

it('changing projects closes the wizard and discards an in-flight bind', async () => {
  const job = deferred<PanelChartState>()
  vi.mocked(chartBind).mockReturnValue(job.promise)
  useChartBindStore.setState({ open: true, file: 'chart.svg', projectId: null })
  const running = useChartBindStore.getState().confirmBinding()
  setCurrentProjectId('other-project')
  job.resolve(CHART)
  await running
  expect(useChartBindStore.getState().open).toBe(false)
  expect(useChartBindStore.getState().result).toBeNull()
  expect(chartCompute).not.toHaveBeenCalled()
})

describe('chartBindStore', () => {
  it('openFor loads the scene and lands on the data step', async () => {
    vi.mocked(chartInspect).mockResolvedValue(SCENE)
    await useChartBindStore.getState().openFor(panel())
    const s = useChartBindStore.getState()
    expect(s.open).toBe(true)
    expect(s.step).toBe('data')
    expect(s.scene?.scene_digest).toBe(SCENE.scene_digest)
    expect(s.form.series[0].node).toBe('curve') // 候选识别只辅助：预填第一个图形节点
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
      render_revision: 'rev-1',
      model_digest: 'md',
      binding_digest: 'bd',
      engine: { matplotlib: '3.11.2', numpy: '2.1.0', tavotto: '0.20.0' },
    })
    const store = useChartBindStore.getState()
    useChartBindStore.setState({
      file: 'chart.svg',
      form: {
        ...store.form,
        series: [{ ...store.form.series[0], node: 'curve', xCol: 'x', yCol: 'y' }],
      },
    })
    await useChartBindStore.getState().confirmBinding()
    const s = useChartBindStore.getState()
    expect(s.step).toBe('confirm')
    expect(s.result).toEqual({ ...CHART, formRev: expect.any(Number) })
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
    // 无成功预览：即使有 result 也没有提交资格（F3）
    expect(
      chartBindConfirmReady({
        ...base,
        file: 'chart.svg',
        form: { ...base.form, series: [{ ...base.form.series[0], node: 'curve', xCol: 'x', yCol: 'y' }] },
        result: { ...staleChart, formRev: base.formRev } as never,
        previewSvg: null,
        previewOk: false,
      }),
    ).toBe(false)
    expect(
      chartBindConfirmReady({
        ...base,
        file: 'chart.svg',
        form: { ...base.form, series: [{ ...base.form.series[0], node: 'curve', xCol: 'x', yCol: 'y' }] },
        result: { ...staleChart, formRev: base.formRev } as never,
        previewSvg: '<svg/>',
        previewOk: true,
      }),
    ).toBe(true)
    expect(
      chartBindConfirmReady({
        ...base,
        file: 'chart.svg',
        form: {
          ...base.form,
          series: [{ ...base.form.series[0], kind: 'image' }],
          gridText: '',
        },
      }),
    ).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// NEXT_PHASE R00：F1–F3 状态缺陷回归（当前实现 xfail；R04 修复后摘标记）
// ---------------------------------------------------------------------------

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const staleChart = {
  model: { rev: 1, scene_digest: 'old-scene', data_digest: 'old-data' },
  binding: { model_rev: 1 },
  formRev: 0,
} as PanelChartState & { formRev: number }

describe('chart bind state safety (F1-F3)', () => {
  it('F1: keeps B inventory when A inspect resolves after opening B', async () => {
    const a = deferred<SceneInventory>()
    const b = deferred<SceneInventory>()
    vi.mocked(chartInspect).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    useChartBindStore.getState().openFor(panel('A'))
    useChartBindStore.getState().openFor(panel('B'))
    b.resolve({ ...SCENE, scene_digest: 'B' })
    await Promise.resolve()
    a.resolve({ ...SCENE, scene_digest: 'A' })
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.file).toBe('B.svg')
    expect(s.scene?.scene_digest).toBe('B')
  })

  it('F2: invalidates confirmed result and preview when the data form changes', () => {
    useChartBindStore.setState({
      file: 'A.svg',
      step: 'confirm',
      result: staleChart,
      previewSvg: '<svg id="old"/>',
      form: {
        ...DEFAULT_FORM,
        series: [{ ...DEFAULT_SERIES, node: 'curve', xCol: 'x', yCol: 'y' }],
        datasetText: 'x,y\n1,2',
      },
    })
    useChartBindStore.getState().patchForm({ datasetText: 'x,y\n1,999' })
    const s = useChartBindStore.getState()
    expect(s.result).toBeNull()
    expect(s.previewSvg).toBeNull()
  })

  it('F3: does not permit final confirmation after replacement preview fails', async () => {
    useChartBindStore.setState({
      file: 'A.svg',
      step: 'confirm',
      result: { ...staleChart, formRev: useChartBindStore.getState().formRev },
      previewSvg: '<svg id="old"/>',
      form: {
        ...DEFAULT_FORM,
        series: [{ ...DEFAULT_SERIES, node: 'curve', xCol: 'x', yCol: 'y' }],
      },
    })
    vi.mocked(chartCompute).mockRejectedValue(
      Object.assign(new Error('wrong source'), { code: 'chart_source_mismatch' }),
    )
    await useChartBindStore.getState().runPreview()
    const s = useChartBindStore.getState()
    expect(chartBindConfirmReady(s)).toBe(false)
    expect(s.previewSvg).toBeNull()
    expect(s.previewOk).toBe(false)
    expect(s.error?.code).toBe('chart_source_mismatch')
  })
})
