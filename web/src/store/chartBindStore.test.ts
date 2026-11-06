// chartBindStore 状态机测试（SG10）：编排正确、判据不做第二份、错误透传。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chartBind, chartCompute, chartInspect } from '@/lib/chartApi'
import type { SceneInventory } from '@/lib/chartApi'
import type { PanelChartState, PanelObject, PanelOverride } from '@/types/document'
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

it('打开有附件的panel时inspect/compute保留同版完整引用，不改为原file计算', async () => {
  const p = panel()
  p.chart = structuredClone(CHART) as unknown as PanelChartState
  p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
    sceneDigest: p.chart.model.scene_digest }
  const original = structuredClone(p.chartSource)
  vi.mocked(chartInspect).mockResolvedValue(SCENE)
  vi.mocked(chartCompute).mockResolvedValue({ svg: '<svg/>', facts: {} } as never)
  useChartBindStore.getState().openFor(p)
  await vi.waitFor(() => expect(useChartBindStore.getState().busy).toBe(false))
  expect(chartInspect).toHaveBeenCalledWith(p.fileId, original)
  p.chartSource.bytesSha256 = 'b'.repeat(64)
  await useChartBindStore.getState().confirmBinding()
  expect(chartCompute).toHaveBeenCalledWith(p.fileId, expect.anything(), {
    render_profile: 'native-manifest/1', overrides: [], chart_source: original,
  })
})

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
    draftSource: 'new',
    draftChart: null,
    draftRender: null,
    resultRender: null,
    documentContext: null,
    panelInputKey: null,
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

function restorableChart(): PanelChartState {
  return {
    model: {
      ...CHART.model,
      axes: [{ id: 'ax1', rect: [20, 30, 200, 150], xlim: [1, 100], ylim: [2, 200],
        xscale: 'log', yscale: 'linear', invert_x: true, invert_y: false }],
      series: [
        { id: 's1', axes: 'ax1', node: 'curve', kind: 'errorbar', x: 'time', y: 'value', yerr: 'error',
          style: { color: '#248acf', linewidth: 2, label: 'sample' } },
        { id: 's2', axes: 'ax1', node: 'heatmap', kind: 'image', x: '-', y: '-', grid: 'grid',
          cbar_node: 'colorbar', style: { cmap: 'viridis', vmin: 0, vmax: 4 } },
      ],
      dataset: { columns: { time: [1, 10], value: [2, 20], error: [0.1, 0.2] },
        grids: { grid: { values: [[1, 2], [3, 4]] } } },
    },
    binding: { ...CHART.binding, entries: [
      { object: 'series:s1', node: 'curve' },
      { object: 'series:s2', node: 'heatmap' },
      { object: 'axes:ax1', node: 'axis-x' },
      { object: 'axes:ax1', node: 'axis-y' },
      { object: 'axes:ax1', node: 'colorbar' },
    ] },
  }
}

const nativeEdits: PanelOverride[] = [
  { gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000', identity: 'owned-line' },
  { gid: 'axes_0', prop: 'xlim', value: [1, 100] },
  { gid: 'axes_0.lines_0', prop: 'color', value: '#0000ff', identity: 'owned-line' },
]
const nativeRender = () => ({ render_profile: 'native-manifest/1' as const, overrides: structuredClone(nativeEdits) })
const computed = () => ({ svg: '<svg/>', facts: {}, scene_digest: '', render_revision: '', model_digest: '', binding_digest: '', engine: CHART.model.engine })

describe('v2 full edit recovery', () => {
  it('keeps the exact ordered edits as a draft, then previews and retains the same native result state', async () => {
    const restored = restorableChart(), render = nativeRender()
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restored, restored_render: render })
    vi.mocked(chartCompute).mockResolvedValue(computed())
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    expect(useChartBindStore.getState().draftRender).toEqual(render)
    expect(chartCompute).not.toHaveBeenCalled()
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(false)
    await useChartBindStore.getState().confirmBinding()
    expect(chartBind).not.toHaveBeenCalled()
    expect(chartCompute).toHaveBeenCalledWith('p1.svg', expect.objectContaining(restored), render)
    expect(useChartBindStore.getState().resultRender).toEqual(render)
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(true)
    expect(render.overrides).toEqual(nativeEdits)
  })

  it.each([
    { overrides: [{ gid: 'axes_0.lines_0', prop: 'linewidth', value: 7 }] as PanelOverride[] },
    { overrides: [] as PanelOverride[] },
  ])('prefers the current panel model and all current ordered edits (%j) over an older embedded package', async ({ overrides }) => {
    const existing = restorableChart()
    existing.model.dataset.columns.value = [99, 100]
    const currentPanel = { ...panel(), chart: existing, overrides: structuredClone(overrides) }
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restorableChart(), restored_render: nativeRender() })
    vi.mocked(chartCompute).mockResolvedValue(computed())
    useChartBindStore.getState().openFor(currentPanel)
    await Promise.resolve()
    await useChartBindStore.getState().confirmBinding()
    expect(chartCompute).toHaveBeenCalledWith('p1.svg', expect.objectContaining(existing), {
      render_profile: 'native-manifest/1', overrides,
    })
    expect(useChartBindStore.getState().resultRender?.overrides).toEqual(overrides)
  })

  it('freezes panel edits when opening and does not let later caller mutation change the preview', async () => {
    const p = { ...panel(), chart: restorableChart(), overrides: structuredClone(nativeEdits) }
    vi.mocked(chartInspect).mockResolvedValue(SCENE)
    vi.mocked(chartCompute).mockResolvedValue(computed())
    useChartBindStore.getState().openFor(p)
    p.overrides[0].value = '#00ff00'
    await Promise.resolve()
    await useChartBindStore.getState().confirmBinding()
    expect(vi.mocked(chartCompute).mock.calls[0]?.[2]?.overrides).toEqual(nativeEdits)
  })

  it('sends the original chart as backend ownership evidence after form changes, retaining all edits', async () => {
    const restored = restorableChart()
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restored, restored_render: nativeRender() })
    vi.mocked(chartCompute).mockResolvedValue(computed())
    vi.mocked(chartBind).mockResolvedValue(CHART)
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    useChartBindStore.getState().patchForm({ datasetText: 'time\tvalue\terror\n1\t9\t1\n10\t90\t2' })
    await useChartBindStore.getState().confirmBinding()
    expect(chartCompute).toHaveBeenCalledWith('p1.svg', expect.objectContaining(CHART), {
      ...nativeRender(), previous_chart: restored,
    })
    expect(useChartBindStore.getState().resultRender?.overrides).toEqual(nativeEdits)
  })

  it('keeps edits and draft when the backend refuses ownership migration, without granting confirmation', async () => {
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restorableChart(), restored_render: nativeRender() })
    vi.mocked(chartBind).mockResolvedValue(CHART)
    vi.mocked(chartCompute).mockRejectedValue(Object.assign(new Error('chart_binding_stale'), { code: 'chart_binding_stale' }))
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    useChartBindStore.getState().patchSeries(0, { node: 'another-node' })
    await useChartBindStore.getState().confirmBinding()
    expect(useChartBindStore.getState().draftRender?.overrides).toEqual(nativeEdits)
    expect(useChartBindStore.getState().form.series[0].node).toBe('another-node')
    expect(useChartBindStore.getState().error?.code).toBe('chart_binding_stale')
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(false)
  })

  it('explicitly sends null prior science for existing edits on an unmodeled ordinary SVG', async () => {
    vi.mocked(chartInspect).mockResolvedValue(SCENE)
    vi.mocked(chartBind).mockResolvedValue(CHART)
    vi.mocked(chartCompute).mockResolvedValue(computed())
    useChartBindStore.getState().openFor({ ...panel(), overrides: structuredClone(nativeEdits) })
    await Promise.resolve()
    await useChartBindStore.getState().confirmBinding()
    expect(vi.mocked(chartCompute).mock.calls[0]?.[2]).toEqual({ ...nativeRender(), previous_chart: null })
  })

  it('late v2 preview after closing cannot revive result or restore edits', async () => {
    const pending = deferred<ReturnType<typeof computed>>()
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restorableChart(), restored_render: nativeRender() })
    vi.mocked(chartCompute).mockReturnValue(pending.promise)
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    const preview = useChartBindStore.getState().confirmBinding()
    useChartBindStore.getState().close()
    pending.resolve(computed())
    await preview
    expect(useChartBindStore.getState().draftRender).toBeNull()
    expect(useChartBindStore.getState().resultRender).toBeNull()
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(false)
  })
})

describe('self-contained SVG recovery draft', () => {
  it('fills data, calibration, series styles and grid without granting confirmation', async () => {
    const restored = restorableChart()
    const before = structuredClone(restored)
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restored })
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.draftSource).toBe('svg')
    expect(s.form.rect).toEqual([20, 30, 200, 150])
    expect(s.form.x).toEqual({ p1_user: 0, p1_value: 100, p2_user: 1, p2_value: 1, scale: 'log' })
    expect(s.form.y).toEqual({ p1_user: 0, p1_value: 2, p2_user: 1, p2_value: 200, scale: 'linear' })
    expect(s.form.datasetText).toBe('time\tvalue\terror\n1\t2\t0.1\n10\t20\t0.2')
    expect(s.form.series).toEqual([
      expect.objectContaining({ node: 'curve', kind: 'errorbar', yerrCol: 'error', style: restored.model.series[0].style }),
      expect.objectContaining({ node: 'heatmap', kind: 'image', cbarNode: 'colorbar', style: restored.model.series[1].style }),
    ])
    expect(s.form.axesNodes).toEqual(['axis-x', 'axis-y'])
    expect(s.form.gridText).toBe('1\t2\n3\t4')
    expect(s.result).toBeNull()
    expect(chartBindConfirmReady(s)).toBe(false)
    expect(chartBind).not.toHaveBeenCalled()
    expect(chartCompute).not.toHaveBeenCalled()
    expect(restored).toEqual(before)
  })

  it('keeps this panel edits instead of replacing them with the embedded older chart', async () => {
    const existing = restorableChart()
    existing.model.axes[0].xlim = [10, 1000]
    existing.model.dataset.columns.value = [50, 100]
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restorableChart() })
    useChartBindStore.getState().openFor({ ...panel(), chart: existing })
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.draftSource).toBe('panel')
    expect(s.form.x.p1_value).toBe(1000)
    expect(s.form.datasetText).toContain('1\t50\t0.1')
  })

  it('previews the unchanged recovered model intact and only rebuilds after form edits', async () => {
    const restored = restorableChart()
    restored.model.axes[0].xlabel = 'preserved axis label'
    restored.model.dataset.grids!.unused = { values: [[8, 9]] }
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restored })
    vi.mocked(chartCompute).mockResolvedValue({ svg: '<svg/>', facts: {}, scene_digest: '', render_revision: '', model_digest: '', binding_digest: '', engine: CHART.model.engine })
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    await useChartBindStore.getState().confirmBinding()
    expect(chartBind).not.toHaveBeenCalled()
    expect(chartCompute).toHaveBeenCalledWith('p1.svg', expect.objectContaining(restored))
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(true)
    expect(useChartBindStore.getState().result?.model.dataset.grids?.unused).toEqual({ values: [[8, 9]] })
    useChartBindStore.getState().patchForm({ gridText: '4\t3\n2\t1' })
    vi.mocked(chartBind).mockResolvedValue(CHART)
    await useChartBindStore.getState().confirmBinding()
    expect(chartBind).toHaveBeenCalledWith(expect.objectContaining({
      axes_nodes: ['axis-x', 'axis-y'], grid_text: '4\t3\n2\t1',
      series: [expect.objectContaining({ node: 'curve' }), expect.objectContaining({ cbar_node: 'colorbar' })],
    }))
  })

  it('retains delimiters, quotes and line breaks in restored table cells', async () => {
    const restored = restorableChart()
    // 这些字符串是合法类别列；ChartDataset 的旧数字列类型尚未表达该后端合同。
    const categories = ['A\tB', 'C\n"D"']
    Object.assign(restored.model.dataset.columns, { category: categories })
    restored.model.axes[0].kind = 'categorical'
    restored.model.axes[0].categories = categories
    restored.model.series[0].x = 'category'
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restored })
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.form.xKind).toBe('categorical')
    expect(s.form.categoriesCol).toBe('category')
    expect(s.form.datasetText).toContain('"A\tB"')
    expect(s.form.datasetText).toContain('"C\n""D"""')
    expect(restored.model.dataset.columns).toHaveProperty('category', categories)
  })

  it('does not overwrite changed recovery drafts when the scene is loaded again', async () => {
    vi.mocked(chartInspect).mockResolvedValue({ ...SCENE, restored_chart: restorableChart() })
    useChartBindStore.getState().openFor(panel())
    await Promise.resolve()
    useChartBindStore.getState().patchForm({ datasetText: 'time\tvalue\n1\t99' })
    await useChartBindStore.getState().loadScene()
    expect(useChartBindStore.getState().form.datasetText).toBe('time\tvalue\n1\t99')
    expect(useChartBindStore.getState().draftSource).toBe('svg')
  })

  it('late embedded data from another panel cannot mark the active ordinary SVG as recovered', async () => {
    const first = deferred<SceneInventory>()
    vi.mocked(chartInspect).mockReturnValueOnce(first.promise).mockResolvedValueOnce(SCENE)
    useChartBindStore.getState().openFor(panel('old'))
    useChartBindStore.getState().openFor(panel('current'))
    await Promise.resolve()
    first.resolve({ ...SCENE, restored_chart: restorableChart() })
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.file).toBe('current.svg')
    expect(s.draftSource).toBe('new')
    expect(s.form.datasetText).toBe('')
  })

  it('project changes invalidate pending restoration without opening or confirming it', async () => {
    const pending = deferred<SceneInventory>()
    vi.mocked(chartInspect).mockReturnValueOnce(pending.promise)
    useChartBindStore.getState().openFor(panel())
    setCurrentProjectId('different-project')
    pending.resolve({ ...SCENE, restored_chart: restorableChart() })
    await Promise.resolve()
    const s = useChartBindStore.getState()
    expect(s.open).toBe(false)
    expect(s.scene).toBeNull()
    expect(s.draftSource).toBe('new')
    expect(s.result).toBeNull()
  })

  it('sends categorical column evidence without conflicting numeric x calibration', async () => {
    vi.mocked(chartBind).mockImplementation(async request => {
      // 与后端 categorical 合同相同：类别位置由数据列决定，不能同时给 numeric x。
      expect(request).not.toHaveProperty('x')
      expect(request).toMatchObject({ x_kind: 'categorical', categories_col: 'category' })
      return CHART
    })
    vi.mocked(chartCompute).mockResolvedValue({ svg: '<svg/>', facts: {}, scene_digest: '', render_revision: '', model_digest: '', binding_digest: '', engine: CHART.model.engine })
    useChartBindStore.setState({ file: 'category.svg' })
    useChartBindStore.getState().patchForm({ xKind: 'categorical', categoriesCol: 'category',
      datasetText: 'category\tvalue\nA\t1\nB\t2', series: [{ node: 'bars', kind: 'bar', xCol: 'category', yCol: 'value' }] })
    await useChartBindStore.getState().confirmBinding()
    expect(chartBind).toHaveBeenCalledOnce()
    expect(chartCompute).toHaveBeenCalledOnce()
    expect(chartBindConfirmReady(useChartBindStore.getState())).toBe(true)
  })
})

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
