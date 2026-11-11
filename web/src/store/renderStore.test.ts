/**
 * 渲染态按「文件 + 变体」分键（Phase F）。
 *
 * 关键约定三条：同一文件的不同 overrides 各存各的；SVG 与 manifest 来自
 * **同一次响应**（inline_svg，不再第二跳 GET）；自己那份还没画出来时
 * 退回该文件最近画好的那张，而不是让画布闪回磁盘原图。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions, Manifest } from '@/lib/api'
import {
  exactPanelRender,
  exactPanelManifest,
  panelDisplayView,
  panelRender,
  renderKey,
  renderFailureOwner,
  renderKeyOf,
  renderRequestIdentity,
  structuralPanelManifest,
  settleRenderFailureToast,
  useRenderStore,
} from './renderStore'
import { msg } from '@/i18n'
import { currentProjectId } from '@/lib/session'
import { useUiStore } from '@/store/uiStore'
import { EDITOR_SVG_HARD_LIMIT_BYTES } from '@/lib/previewBudget'
import type { PanelObject, PanelChartState } from '@/types/document'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const engineRender = vi.fn()

function chartPanel(id = 'chart', upper?: number): PanelObject {
  const v = chartVectors.vectors.find(v => v.expect === 'ok')!
  const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
  if (upper != null) chart.model.axes[0].xlim[1] = upper
  return { ...panel(id, 'chart.svg'), script: null, fileKind: 'svg', chart }
}

const renderChart = (p: PanelObject) => useRenderStore.getState().render(
  p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'foreground', p.chart,
)
const chartResponse = (revision: string) => ({ rev: 1, chart_revision: revision,
  manifest: manifest(revision), svg: `<svg id="${revision}"/>` })

describe('chart 与普通渲染共用配对状态机', () => {
  const sourcePanel = () => {
    const p = chartPanel()
    p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: p.chart!.model.scene_digest }
    return p
  }
  const renderSource = (p: PanelObject) => useRenderStore.getState().render(p.fileId, p.overrides,
    undefined, undefined, undefined, undefined, 'foreground', p.chart, p.chartSource)

  it('同模型不同INPUT附件有独立键与显示/结构/几何身份，绝不借同文件来源', async () => {
    const p = sourcePanel(), other = structuredClone(p)
    other.chartSource!.bytesSha256 = 'b'.repeat(64)
    expect(renderKeyOf(p)).not.toBe(renderKeyOf(other))
    expect(renderKeyOf(p)).not.toBe(renderKeyOf({ ...p, chartSource: undefined }))
    engineRender.mockResolvedValue(chartResponse('bound-source'))
    await renderSource(p)
    expect(engineRender.mock.calls[0][2].chart_source).toEqual(p.chartSource)
    const state = useRenderStore.getState()
    expect(exactPanelManifest(state, p)).not.toBeNull()
    for (const candidate of [other, { ...p, chartSource: undefined }]) {
      expect(panelRender(state, candidate)).toBeUndefined()
      expect(structuralPanelManifest(state, candidate)).toBeNull()
      expect(exactPanelManifest(state, candidate)).toBeNull()
    }
  })

  it('发送时冻结ref，晚到旧附件响应不能给新引用授予exact资格', async () => {
    const p = sourcePanel(), original = structuredClone(p)
    let finish!: (v: unknown) => void
    engineRender.mockImplementationOnce(() => new Promise((r) => { finish = r }))
    const pending = renderSource(p)
    p.chartSource!.bytesSha256 = 'b'.repeat(64)
    expect(engineRender.mock.calls[0][2].chart_source).toEqual(original.chartSource)
    finish(chartResponse('old-source'))
    await pending
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBeNull()
    expect(exactPanelManifest(useRenderStore.getState(), original)).not.toBeNull()
    engineRender.mockResolvedValue(chartResponse('new-source'))
    await renderSource(p)
    expect(exactPanelManifest(useRenderStore.getState(), p)).not.toBeNull()
  })

  it('普通键保持原样；模型数据/范围/样式/绑定任一变化都有独立完整身份', () => {
    expect(renderKey('plot.pdf', [])).toBe('plot.pdf []')
    const a = chartPanel()
    expect(renderRequestIdentity(a.overrides)).toBe('[]')
    const initial = renderKeyOf(a)
    const variants = [
      (p: PanelObject) => { p.chart!.model.axes[0].xlim[1] += 10 },
      (p: PanelObject) => { p.chart!.model.series[0].style = { color: '#ff0000' } },
      (p: PanelObject) => { p.chart!.model.dataset.columns['extra'] = [2] },
      (p: PanelObject) => { p.chart!.binding.model_rev += 1 },
    ]
    for (const change of variants) {
      const copy = structuredClone(a)
      change(copy)
      expect(renderKeyOf(copy)).not.toBe(initial)
    }
    expect(renderKeyOf(a)).not.toBe(renderKey(a.fileId, a.overrides))
  })

  it('完整 chart 与 patches 进入原 engineRender，返回SVG/manifest/revision同存', async () => {
    const p = chartPanel()
    p.overrides = [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f00' }]
    engineRender.mockResolvedValue(chartResponse('paired'))
    await renderChart(p)
    expect(engineRender.mock.calls[0][0]).toBe(p.fileId)
    expect(engineRender.mock.calls[0][1]).toEqual(p.overrides)
    expect(engineRender.mock.calls[0][2].chart).toEqual(p.chart)
    const entry = useRenderStore.getState().get(renderKeyOf(p))
    expect(entry).toMatchObject({ chartRevision: 'paired', chartRequest: JSON.stringify(p.chart),
      manifest: manifest('paired'), lastPatches: renderRequestIdentity(p.overrides, p.chart) })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(entry.manifest)
  })

  it('同文件另一chart的SVG、结构与几何均不能作为文件级退路，普通面板也不借chart', async () => {
    const a = chartPanel('a'), b = chartPanel('b', 50)
    engineRender.mockResolvedValue(chartResponse('A'))
    await renderChart(a)
    const state = useRenderStore.getState()
    for (const target of [b, { ...a, chart: undefined }]) {
      expect(panelRender(state, target)).toBeUndefined()
      expect(structuralPanelManifest(state, target)).toBeNull()
      expect(exactPanelManifest(state, target)).toBeNull()
      expect(panelDisplayView(state, target).kind).toBe('empty')
    }
    const edited = { ...a, overrides: [{ gid: 'axes_0', prop: 'ylim', value: [1, 20] }] }
    expect(panelDisplayView(state, edited).kind).toBe('fallback')
    expect(exactPanelManifest(state, edited)).toBeNull()
  })

  it('迟到A仅填A条目，不能覆盖已落地B的latest或几何', async () => {
    const a = chartPanel('a'), b = chartPanel('b', 50)
    let finishA!: (v: unknown) => void, finishB!: (v: unknown) => void
    engineRender.mockReturnValueOnce(new Promise(r => { finishA = r })).mockReturnValueOnce(new Promise(r => { finishB = r }))
    const pendingA = renderChart(a), pendingB = renderChart(b)
    finishB(chartResponse('B')); await pendingB
    finishA(chartResponse('A')); await pendingA
    const state = useRenderStore.getState()
    expect(state.latest[a.fileId]).toBe(renderKeyOf(b))
    expect(exactPanelManifest(state, b)?.stem).toBe('B')
    expect(exactPanelManifest(state, a)?.stem).toBe('A')
  })

  it('chart入队即冻结完整JSON；调用方改动不能重写在途内容及配对身份', async () => {
    const p = chartPanel(), original = structuredClone(p)
    let finish!: (v: unknown) => void
    engineRender.mockReturnValue(new Promise(r => { finish = r }))
    const pending = renderChart(p)
    p.chart!.model.axes[0].xlim[1] += 100
    expect(engineRender.mock.calls[0][2].chart).toEqual(original.chart)
    finish(chartResponse('original')); await pending
    expect(useRenderStore.getState().get(renderKeyOf(original)).lastPatches).toBe(renderRequestIdentity(original.overrides, original.chart))
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBeNull()
  })

  it('clear换代后chart旧响应一个条目也不落库', async () => {
    let finish!: (v: unknown) => void
    engineRender.mockReturnValue(new Promise(r => { finish = r }))
    const pending = renderChart(chartPanel())
    useRenderStore.getState().clear()
    finish(chartResponse('old-project')); await pending
    expect(useRenderStore.getState().byKey).toEqual({})
  })

  it('没有后端配对身份的旧render响应不授予chart几何资格', async () => {
    const p = chartPanel()
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('unqualified'), svg: '<svg/>' })
    await renderChart(p)
    expect(useRenderStore.getState().get(renderKeyOf(p))).toMatchObject({ status: 'error', code: 'chart_render_pair_missing', manifest: null })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBeNull()
  })

  it.each(['chart_source_changed', 'chart_render_pair_missing', 'another_chart_failure'])(
    'chart同键成功后终态失败 %s：保留显示与结构，撤销几何资格，成功重试才恢复',
    async (code) => {
      const { EngineError } = await import('@/lib/api')
      const p = chartPanel()
      engineRender.mockResolvedValueOnce(chartResponse('before-failure'))
      await renderChart(p)
      const previous = useRenderStore.getState().get(renderKeyOf(p))
      expect(exactPanelRender(useRenderStore.getState(), p)).toBe(previous)

      if (code === 'chart_render_pair_missing') {
        engineRender.mockResolvedValueOnce({ rev: 2, manifest: manifest('unpaired'), svg: '<svg/>' })
      } else {
        engineRender.mockRejectedValueOnce(new EngineError('chart failed', '', code, ''))
      }
      await renderChart(p)

      const failed = useRenderStore.getState()
      expect(failed.get(renderKeyOf(p))).toMatchObject({ status: 'error', code })
      expect(exactPanelRender(failed, p)).toBeNull()
      expect(exactPanelManifest(failed, p)).toBeNull()
      expect(panelRender(failed, p)?.svg).toBe(previous.svg)
      expect(structuralPanelManifest(failed, p)).toBe(previous.manifest)
      const display = panelDisplayView(failed, p)
      expect(display.kind).toBe('fallback')
      expect(display).not.toHaveProperty('manifest')
      expect(display.svg).toBe(previous.svg)

      let finish!: (value: unknown) => void
      engineRender.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
      const retry = renderChart(p)
      expect(useRenderStore.getState().get(renderKeyOf(p)).status).toBe('rendering')
      expect(exactPanelRender(useRenderStore.getState(), p)).toBeNull()
      finish(chartResponse('after-retry'))
      await retry
      expect(exactPanelManifest(useRenderStore.getState(), p)?.stem).toBe('after-retry')
      expect(useRenderStore.getState().get(renderKeyOf(p)).stale).toBe(false)
    },
  )

  it('chart终态失败保留已请求身份，重复同步不会无限重试', async () => {
    const { EngineError } = await import('@/lib/api')
    const { syncEngine } = await import('@/hooks/useEngineSync')
    const p = chartPanel()
    vi.useFakeTimers()
    try {
      engineRender.mockResolvedValueOnce(chartResponse('previous'))
      await renderChart(p)
      engineRender.mockRejectedValueOnce(new EngineError('source changed', '', 'chart_source_changed', ''))
      await renderChart(p)
      expect(useRenderStore.getState().get(renderKeyOf(p)).stale).toBe(true)
      for (let i = 0; i < 5; i++) {
        syncEngine([p], p.id)
        await vi.advanceTimersByTimeAsync(1000)
      }
      expect(engineRender).toHaveBeenCalledTimes(2)
      expect(exactPanelRender(useRenderStore.getState(), p)).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('chart请求被同键排队重试取代时保留旧几何，等待排队结果决定终态', async () => {
    const { EngineError } = await import('@/lib/api')
    const p = chartPanel()
    engineRender.mockResolvedValueOnce(chartResponse('previous'))
    await renderChart(p)
    const previous = exactPanelManifest(useRenderStore.getState(), p)
    let rejectFirst!: (error: unknown) => void, finishLatest!: (value: unknown) => void
    engineRender
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockReturnValueOnce(new Promise(resolve => { finishLatest = resolve }))
    const pending = renderChart(p)
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(previous)
    await renderChart(p)
    rejectFirst(new EngineError('superseded failure', '', 'chart_source_changed', ''))
    await vi.waitFor(() => expect(engineRender).toHaveBeenCalledTimes(3))
    expect(useRenderStore.getState().get(renderKeyOf(p))).toMatchObject({ status: 'rendering', stale: false })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(previous)
    finishLatest(chartResponse('latest'))
    await pending
    expect(exactPanelManifest(useRenderStore.getState(), p)?.stem).toBe('latest')
  })

  it('chart后台暂缓不算终态失败，保留同版显示与几何', async () => {
    const { EngineError } = await import('@/lib/api')
    const { RENDER_DEFERRED_CODE } = await import('@/lib/renderPurpose')
    const p = chartPanel()
    engineRender.mockResolvedValueOnce(chartResponse('previous'))
    await renderChart(p)
    const previous = exactPanelManifest(useRenderStore.getState(), p)
    engineRender.mockRejectedValueOnce(new EngineError('deferred', '', RENDER_DEFERRED_CODE, ''))
    await useRenderStore.getState().render(p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'neighbour', p.chart)
    expect(useRenderStore.getState().get(renderKeyOf(p))).toMatchObject({ status: 'ready', stale: false, code: '' })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(previous)
  })

  it('普通Python同键失败仍保留这一版最后成功的几何资格', async () => {
    const { EngineError } = await import('@/lib/api')
    const p = panel('python', 'plot.pdf')
    engineRender.mockResolvedValueOnce({ rev: 1, manifest: manifest('python'), svg: '<svg/>' })
    await useRenderStore.getState().render(p.fileId, p.overrides)
    const previous = exactPanelManifest(useRenderStore.getState(), p)
    engineRender.mockRejectedValueOnce(new EngineError('script failed', '', 'script_error', ''))
    await useRenderStore.getState().render(p.fileId, p.overrides)
    expect(useRenderStore.getState().get(renderKeyOf(p))).toMatchObject({ status: 'error', stale: false })
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBe(previous)
    expect(panelDisplayView(useRenderStore.getState(), p).kind).toBe('exact')
  })
})

describe('同次渲染正证的接线', () => {
  it('普通渲染请求正证并按本次 P 记录，历史来源不请求也不记录', async () => {
    const { setCurrentProjectId } = await import('@/lib/session')
    const { clearFigureExecutionProofs, figureExecutionProofFor } = await import('@/lib/figureExecutionProof')
    clearFigureExecutionProofs()
    setCurrentProjectId('proj-proof')
    try {
      const p = panel('proof', 'Fig1.pdf', [{ gid: 'g', prop: 'linewidth', value: 2 }])
      const proof = {
        version: 1, sourceId: 'Fig1.pdf', receiptId: 'r-1',
        executionOwner: { script: 'plot.py', run_config: null }, patchHash: 'sha256:p',
      }
      engineRender.mockResolvedValueOnce({
        rev: 1, manifest: manifest('Fig1'), svg: '<svg/>', figure_execution: proof,
      })
      await useRenderStore.getState().render(p.fileId, p.overrides)
      expect(engineRender.mock.calls[0][2].includeFigureExecution).toBe(true)
      expect(figureExecutionProofFor('proj-proof', 'Fig1.pdf', p.overrides)).toEqual(proof)

      // A historical source already carries its own identity; it neither asks
      // for proof nor overwrites the proof of the current source.
      const origin = {
        version: 1 as const,
        snapshot: {
          version: 1 as const, sourceId: 'Fig1.pdf', patchHash: 'sha256:p', receiptId: 'r-1',
          snapshotId: 's-1', executionOwner: { script: 'plot.py', run_config: null },
        },
        patches: structuredClone(p.overrides),
      }
      engineRender.mockResolvedValueOnce({ rev: 2, manifest: manifest('Fig1'), svg: '<svg/>' })
      await useRenderStore.getState().render(
        p.fileId, p.overrides, undefined, undefined, undefined, undefined, 'foreground',
        undefined, undefined, origin,
      )
      expect(engineRender.mock.calls[1][2].includeFigureExecution).toBeUndefined()
      expect(figureExecutionProofFor('proj-proof', 'Fig1.pdf', p.overrides)).toEqual(proof)
    } finally {
      clearFigureExecutionProofs()
      setCurrentProjectId(null)
    }
  })
})

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const manifest = (stem: string): Manifest =>
  ({ stem, size_mm: [80, 60], elements: [] }) as unknown as Manifest

const panel = (id: string, fileId: string, overrides: unknown[] = []): PanelObject =>
  ({
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
    overrides,
  }) as unknown as PanelObject

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
})

describe('二道闸：超大 SVG 不进 store（ADR 0022）', () => {
  it('后端说 raster 时不存 svg，但 manifest / status 照常', async () => {
    const p = panel('a', 'Fig1.pdf')
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      preview: {
        mode: 'raster',
        reason: 'svg_hard_limit',
        svg_bytes: 126_132_735,
        rasterized_artist_count: 0,
      },
    })
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKeyOf(p))
    // **不是渲染失败**：manifest 在，状态是 ready，只是没有矢量图
    expect(entry.status).toBe('ready')
    expect(entry.manifest).not.toBeNull()
    expect(entry.svg).toBeNull()
    expect(entry.preview.mode).toBe('raster')
  })

  it('后端异常地给了一份超大 svg：当场丢掉，绝不 prepareSvg 后存进来', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      // 老后端 / 绕过了闸的后端：没有 preview 字段，svg 却大得离谱
      svg: `<svg>${'x'.repeat(EDITOR_SVG_HARD_LIMIT_BYTES)}</svg>`,
    })
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKey('Fig1.pdf', []))
    expect(entry.svg).toBeNull()
    expect(entry.preview.mode).toBe('raster')
    expect(entry.preview.reason).toBe('fallback')
  })

  it('raster 之后再画成矢量：svg 回得来（这道闸不是单向门）', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      preview: {
        mode: 'raster',
        reason: 'svg_hard_limit',
        svg_bytes: 1e8,
        rasterized_artist_count: 0,
      },
    })
    await useRenderStore.getState().render('Fig1.pdf', [])
    engineRender.mockResolvedValue({
      rev: 2,
      manifest: manifest('Fig1'),
      warnings: [],
      svg: '<svg id="back"/>',
    })
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKey('Fig1.pdf', []))
    expect(entry.svg).toContain('id="back"')
    expect(entry.preview.mode).toBe('vector')
  })
})

describe('失败 toast：本变体落定之后再问一次（Codex #710 第四轮）', () => {
  it('SSE 的 render.done 先于响应到：那一刻撤不掉，响应回来、busy 松开后撤掉', async () => {
    useUiStore.getState().setStatus(msg('status.renderFailed', { name: 'Fig1' }, 'workspace'), 'error', {
      owner: renderFailureOwner(currentProjectId(), 'Fig1.pdf'),
    })
    let resolve: (v: unknown) => void = () => {}
    engineRender.mockReturnValue(new Promise((r) => (resolve = r)))
    const pending = useRenderStore.getState().render('Fig1.pdf', [])
    settleRenderFailureToast(currentProjectId(), 'Fig1.pdf') // SSE 的 done 先到
    expect(useUiStore.getState().statusTone).toBe('error')
    resolve({ rev: 1, manifest: manifest('Fig1'), warnings: [] })
    await pending
    expect(useUiStore.getState().status).toBeNull()
  })
})

describe('变体键', () => {
  it('同文件不同 overrides 是不同的键，相同 overrides 是同一个键', () => {
    const a = panel('a', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#f00' }])
    const b = panel('b', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#00f' }])
    const c = panel('c', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#f00' }])
    expect(renderKeyOf(a)).not.toBe(renderKeyOf(b))
    expect(renderKeyOf(a)).toBe(renderKeyOf(c))
  })

  it('文件名里带空格也不会撞键（变体串必然以 [ 开头）', () => {
    expect(renderKey('my figs/a [1].pdf', [])).not.toBe(renderKey('my figs/a', [1, []]))
  })

  it('坐标合同参与身份：/1 面板逐字不变，/2 面板同 chart 不同 profile 是不同键', () => {
    const base = panel('a', 'native.pdf')
    const chart = { model: { schema: 1, rev: 1, scene_digest: 's' }, binding: { model_rev: 1, scene_digest: 's', entries: [] } } as unknown as PanelChartState
    const one = { ...base, chart } as PanelObject
    const two = { ...base, chart, chartFrame: { version: 1 as const, renderProfile: 'native-manifest/2' as const, originPt: [0, 0] as [number, number] } } as PanelObject
    // /1（无 chartFrame）身份不含 render_profile：旧形状逐字保持
    expect(renderRequestIdentity([], chart)).toBe(JSON.stringify({ patches: [], chart }))
    expect(renderRequestIdentity([], chart, undefined, undefined, undefined)).toBe(JSON.stringify({ patches: [], chart }))
    expect(renderKeyOf(one)).toBe(renderKeyOf({ ...one }))
    expect(renderKeyOf(one)).not.toBe(renderKeyOf(two))
  })
})

describe('render：SVG 与 manifest 同一次响应', () => {
  it('inline 回来的 SVG 直接入库（去掉 width/height 铺满容器）', async () => {
    engineRender.mockResolvedValue({
      rev: 3,
      manifest: manifest('Fig1'),
      svg: '<svg width="216pt" height="144pt" viewBox="0 0 216 144"><g/></svg>',
    })

    await useRenderStore.getState().render('Fig1.pdf', [])

    const st = useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])]
    expect(st.rev).toBe(3)
    expect(st.svg).toContain('preserveAspectRatio="none"')
    expect(st.svg).not.toContain('width="216pt"')
    // 一次请求拿齐，不再有第二跳
    expect(engineRender).toHaveBeenCalledTimes(1)
  })

  it('两个变体各存各的，互不覆盖', async () => {
    const v1 = [{ gid: 'g', prop: 'text', value: 'A' }]
    const v2 = [{ gid: 'g', prop: 'text', value: 'B' }]
    engineRender.mockImplementation(async (_id: string, patches: { value: string }[]) => ({
      rev: 1,
      manifest: manifest(`Fig1-${patches[0].value}`),
      svg: `<svg>${patches[0].value}</svg>`,
    }))

    await useRenderStore.getState().render('Fig1.pdf', v1)
    await useRenderStore.getState().render('Fig1.pdf', v2)

    const { byKey } = useRenderStore.getState()
    expect(byKey[renderKey('Fig1.pdf', v1)].manifest?.stem).toBe('Fig1-A')
    expect(byKey[renderKey('Fig1.pdf', v2)].manifest?.stem).toBe('Fig1-B')
  })

  it('降质渲染把 preview_dpi 带下去并记在条目上', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [], 100)
    expect(engineRender.mock.calls[0][2]).toMatchObject({ previewDpi: 100 })
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])].previewDpi).toBe(100)

    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])].previewDpi).toBeNull()
  })
})

describe('panelRender：新变体还没画出来时接着显示上一张', () => {
  it('自己那份没有 manifest 就退回该文件最近画好的那份', async () => {
    engineRender.mockResolvedValue({ rev: 2, manifest: manifest('Fig1'), svg: '<svg>old</svg>' })
    await useRenderStore.getState().render('Fig1.pdf', [])

    // 用户刚改了一个值：新变体只有排期，还没有图
    const next = panel('a', 'Fig1.pdf', [{ gid: 'g', prop: 'text', value: 'new' }])
    useRenderStore.getState().patch(renderKeyOf(next), {
      fileId: 'Fig1.pdf',
      wantPatches: JSON.stringify(next.overrides),
      status: 'rendering',
    })

    const view = panelRender(useRenderStore.getState(), next)
    expect(view?.manifest?.stem).toBe('Fig1')          // 上一张还在
    expect(view?.svg).toContain('old')
    expect(view?.status).toBe('rendering')             // 状态仍是自己的
  })

  it('自己那份画好之后就是 store 里那个对象（引用稳定）', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [])
    const p = panel('a', 'Fig1.pdf', [])
    const st = useRenderStore.getState()
    expect(panelRender(st, p)).toBe(st.byKey[renderKeyOf(p)])
  })
})

describe('building：文件级的构建提示不写进变体条目', () => {
  it('置位/清除都只动 building 表', () => {
    const store = useRenderStore.getState()
    store.patch(renderKey('Fig1.pdf', []), { fileId: 'Fig1.pdf', status: 'ready' })
    store.noteBuilding('Fig1.pdf', { cold: true, cost: 'heavy' })

    let s = useRenderStore.getState()
    expect(s.building['Fig1.pdf']).toEqual({ cold: true, cost: 'heavy', phase: 'rendering', startedAt: expect.any(Number) })
    // 另一个副本的条目没被盖成「渲染中」——那样它会永远转圈（没人来收）
    expect(s.byKey[renderKey('Fig1.pdf', [])].status).toBe('ready')

    store.noteBuilding('Fig1.pdf', null)
    s = useRenderStore.getState()
    expect(s.building['Fig1.pdf']).toBeUndefined()
  })
})

describe('首开确认（U03）：失败路径把后端的「需要输入」交给 envStore，但只交给发请求时那个项目', () => {
  const confirmation = () => ({
    kind: 'workdir' as const,
    code: 'workdir_confirmation_required' as const,
    script: 'scripts/entry.py',
    reason: 'project_root_evidence' as const,
    recommended: 'project_root' as const,
    options: [],
    conflicts: [],
    reads: ['data/points.csv'],
  })

  it('同一个项目：载荷交给 envStore，也留在渲染条目上', async () => {
    const { EngineError } = await import('@/lib/api')
    const { useEnvStore } = await import('@/store/envStore')
    const { setCurrentProjectId } = await import('@/lib/session')
    useEnvStore.setState({ workdirConfirmation: null })
    setCurrentProjectId('proj-a')
    try {
      engineRender.mockRejectedValue(
        new EngineError('要先选目录', '', 'workdir_confirmation_required', '', undefined, undefined, {
          confirmation: confirmation(),
        }),
      )
      await useRenderStore.getState().render('Fig1.pdf', [])
      const entry = useRenderStore.getState().get(renderKeyOf(panel('a', 'Fig1.pdf')))
      expect(entry.status).toBe('error')
      expect(entry.code).toBe('workdir_confirmation_required')
      expect(entry.confirmation).toEqual(confirmation())
      expect(useEnvStore.getState().workdirConfirmation).toEqual(confirmation())
    } finally {
      setCurrentProjectId(null)
      useEnvStore.setState({ workdirConfirmation: null })
    }
  })

  it('在途中切了项目：A 的失败回来时不把 A 的问题弹到 B 上（Codex #456 P1）', async () => {
    const { EngineError } = await import('@/lib/api')
    const { useEnvStore } = await import('@/store/envStore')
    const { setCurrentProjectId } = await import('@/lib/session')
    useEnvStore.setState({ workdirConfirmation: null })
    setCurrentProjectId('proj-a')
    try {
      let reject!: (e: unknown) => void
      engineRender.mockImplementation(
        () =>
          new Promise((_, rej) => {
            reject = rej
          }),
      )
      const pending = useRenderStore.getState().render('Fig1.pdf', [])
      await Promise.resolve()
      setCurrentProjectId('proj-b') // 用户切到了 B
      reject(
        new EngineError('要先选目录', '', 'workdir_confirmation_required', '', undefined, undefined, {
          confirmation: confirmation(),
        }),
      )
      await pending
      expect(useEnvStore.getState().workdirConfirmation).toBeNull()
    } finally {
      setCurrentProjectId(null)
      useEnvStore.setState({ workdirConfirmation: null })
    }
  })
})

describe('native 图与文档不一致（Codex #549 第八轮）：文件级，按响应顺序记', () => {
  const ok = (unrestored?: number) => ({
    rev: 1,
    manifest: manifest('Fig1'),
    warnings: [],
    ...(unrestored === undefined ? {} : { unrestored }),
  })
  const ID = 'runtime:fig.py#Fig1'

  it('unrestored > 0 置位；同一份列表重渲染干净（0）后自动解除', async () => {
    engineRender.mockResolvedValue(ok(2))
    await useRenderStore.getState().render(ID, [])
    expect(useRenderStore.getState().inconsistent[ID]).toBe(true)
    engineRender.mockResolvedValue(ok(0))
    await useRenderStore.getState().render(ID, [{ gid: 'g', prop: 'p', value: 1 }])
    expect(useRenderStore.getState().inconsistent[ID]).toBe(false)
  })

  it('老后端不给 unrestored：按 0 读，不挂标记', async () => {
    engineRender.mockResolvedValue(ok())
    await useRenderStore.getState().render(ID, [])
    expect(useRenderStore.getState().inconsistent[ID]).toBe(false)
  })

  it('后端以 native_figure_inconsistent 拒绝换列表的渲染：同样置位，别的错误码不碰它', async () => {
    const { EngineError } = await import('@/lib/api')
    engineRender.mockRejectedValue(new EngineError('不一致', '', 'native_figure_inconsistent'))
    await useRenderStore.getState().render(ID, [])
    expect(useRenderStore.getState().inconsistent[ID]).toBe(true)
    useRenderStore.getState().clear()
    engineRender.mockRejectedValue(new EngineError('超时', '', 'native_relay_failed'))
    await useRenderStore.getState().render(ID, [])
    expect(useRenderStore.getState().inconsistent[ID]).toBeUndefined()
  })

  it('不按变体记：撤销落到早先画干净的变体（命中缓存、不发请求）时标记还在', async () => {
    engineRender.mockResolvedValue(ok(0))
    await useRenderStore.getState().render(ID, [])
    engineRender.mockResolvedValue(ok(1))
    await useRenderStore.getState().render(ID, [{ gid: 'g', prop: 'p', value: 1 }])
    // 早先那个变体的条目仍是干净的，但这张 Figure 的事实是「不一致」
    expect(useRenderStore.getState().get(renderKey(ID, [])).status).toBe('ready')
    expect(useRenderStore.getState().inconsistent[ID]).toBe(true)
  })

  it('reset / clear 清掉标记（重新运行原命令 = 新会话、新 Figure）', async () => {
    engineRender.mockResolvedValue(ok(1))
    await useRenderStore.getState().render(ID, [])
    useRenderStore.getState().reset(ID)
    expect(useRenderStore.getState().inconsistent[ID]).toBeUndefined()
  })
})

describe('渲染条件在途中变了（改指表，ADR 0106）：invalidateInflight 之前发出的回包丢弃', () => {
  it('旧条件的回包晚到：不清 stale、不当权威；请求被 abort；随后的同键渲染立刻发出', async () => {
    const p = panel('a', 'fig1.pdf')
    engineRender.mockResolvedValueOnce({ rev: 1, manifest: manifest('old'), svg: '<svg>old</svg>' })
    await useRenderStore.getState().render('fig1.pdf', [])
    let release: (v: unknown) => void = () => {}
    let signal: AbortSignal | undefined
    engineRender.mockImplementationOnce(
      (_id: string, _p: unknown[], opts?: EngineRenderOptions) =>
        new Promise((res) => {
          signal = opts?.signal
          release = res
        }),
    )
    const inFlight = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()

    useRenderStore.getState().invalidateInflight()
    useRenderStore.getState().markStale(['fig1.pdf'])
    expect(signal?.aborted).toBe(true)
    release({ rev: 2, manifest: manifest('stale-rule'), svg: '<svg>stale-rule</svg>' })
    await inFlight

    const after = useRenderStore.getState().get(renderKeyOf(p))
    expect(after.stale).toBe(true)
    expect(after.svg ?? '').not.toContain('stale-rule')
    expect(exactPanelManifest(useRenderStore.getState(), p)).toBeNull()

    // 同步器按新条件重排：同键的新请求不排在死掉的旧槽位后面，当场发出
    engineRender.mockResolvedValueOnce({ rev: 3, manifest: manifest('new'), svg: '<svg>new</svg>' })
    await useRenderStore.getState().render('fig1.pdf', [])
    expect(engineRender).toHaveBeenCalledTimes(3)
    const fresh = useRenderStore.getState().get(renderKeyOf(p))
    expect(fresh.stale).toBe(false)
    expect(fresh.svg).toContain('new')
  })
})

describe('后端说这一版按旧改指表画的（input_remap_changed，ADR 0106 §五）', () => {
  it('不留错误块：标 stale，同步器按新表重排', async () => {
    const { EngineError } = await import('@/lib/api')
    const p = panel('a', 'fig1.pdf')
    engineRender.mockRejectedValueOnce(new EngineError('改指表已变', '', 'input_remap_changed'))
    await useRenderStore.getState().render('fig1.pdf', [])
    const entry = useRenderStore.getState().get(renderKeyOf(p))
    expect(entry.status).not.toBe('error')
    expect(entry.error).toBeNull()
    expect(entry.stale).toBe(true)
    expect(entry.lastPatches).toBeNull()
  })
})

describe('项目代际（STATE-06）：clear() 之前发出的渲染，回包不落进换过之后的项目', () => {
  /** 挂起下一次 engineRender，返回它的 resolve / reject 两个把手 */
  const holdNextRender = () => {
    const h: { resolve: (v: unknown) => void; reject: (e: unknown) => void } = {
      resolve: () => {},
      reject: () => {},
    }
    engineRender.mockImplementationOnce(
      () =>
        new Promise((res, rej) => {
          h.resolve = res
          h.reject = rej
        }),
    )
    return h
  }
  const ok = (stem: string) => ({ rev: 7, manifest: manifest(stem), svg: `<svg>${stem}</svg>` })
  // 两个项目里都有一张叫 fig1.pdf 的图、面板都没有 override：变体键完全相同
  const b = panel('pB', 'fig1.pdf')

  it('A 的成功回包在切到 B 之后才到：B 的 byKey / latest / 几何权威里都没有它', async () => {
    const a = holdNextRender()
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()
    useRenderStore.getState().clear() // resetForNewProject() 做的正是这一句

    a.resolve(ok('A-fig1'))
    await pendingA

    const st = useRenderStore.getState()
    expect(st.byKey[renderKeyOf(b)]).toBeUndefined()
    expect(st.latest['fig1.pdf']).toBeUndefined()
    expect(st.recent['fig1.pdf']).toBeUndefined()
    expect(exactPanelManifest(st, b)).toBeNull()
    expect(panelDisplayView(st, b)?.kind).toBe('empty')
  })

  it('B 自己的同键渲染先回来、A 的旧回包后到：B 的几何权威不被覆盖', async () => {
    const a = holdNextRender()
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()
    useRenderStore.getState().clear()

    engineRender.mockResolvedValueOnce({ ...ok('B-fig1'), rev: 1 })
    await useRenderStore.getState().render('fig1.pdf', [])
    expect(exactPanelManifest(useRenderStore.getState(), b)?.stem).toBe('B-fig1')

    a.resolve(ok('A-fig1'))
    await pendingA
    const st = useRenderStore.getState()
    expect(exactPanelManifest(st, b)?.stem).toBe('B-fig1')
    expect(st.byKey[renderKeyOf(b)].rev).toBe(1)
    expect(st.byKey[renderKeyOf(b)].svg).toContain('B-fig1')
  })

  it('A → B → A 来回切：pj 又对上了，旧回包照样作废（只比 pj 挡不住这一条）', async () => {
    const { setCurrentProjectId } = await import('@/lib/session')
    setCurrentProjectId('proj-a')
    try {
      const a = holdNextRender()
      const pendingA = useRenderStore.getState().render('fig1.pdf', [])
      await Promise.resolve()
      setCurrentProjectId('proj-b')
      useRenderStore.getState().clear()
      setCurrentProjectId('proj-a')
      useRenderStore.getState().clear()

      a.resolve(ok('stale-A'))
      await pendingA
      expect(useRenderStore.getState().byKey[renderKeyOf(b)]).toBeUndefined()
      expect(useRenderStore.getState().latest['fig1.pdf']).toBeUndefined()
    } finally {
      setCurrentProjectId(null)
    }
  })

  it('A 的失败回包在切到 B 之后才到：B 里不出现 A 的错误块', async () => {
    const { EngineError } = await import('@/lib/api')
    const a = holdNextRender()
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()
    useRenderStore.getState().clear()

    a.reject(new EngineError('A 的脚本炸了', '', 'script_error', ''))
    await pendingA
    expect(useRenderStore.getState().byKey[renderKeyOf(b)]).toBeUndefined()
  })

  it('对照：没换项目时同一个挂起的回包照常入库（闸不是把所有迟到的都挡了）', async () => {
    const a = holdNextRender()
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()
    a.resolve(ok('A-fig1'))
    await pendingA
    const st = useRenderStore.getState()
    expect(st.byKey[renderKeyOf(b)].manifest?.stem).toBe('A-fig1')
    expect(st.latest['fig1.pdf']).toBe(renderKeyOf(b))
  })

  it('防抖窗口里换了项目：旧项目排下的那次到点也不发、不落进新项目（Codex #597 P2）', async () => {
    // 代际要在**排渲染那一刻**记下，不是到点发出那一刻——到点时 clear() 早换过代了，
    // 那时再取就是新项目的代，旧请求会带着新代际一路畅通地写进新项目
    const { requestRender } = await import('./renderScheduler')
    vi.useFakeTimers()
    try {
      requestRender(panel('pA', 'fig1.pdf'), 'defer')
      useRenderStore.getState().clear()
      engineRender.mockResolvedValue(ok('A-fig1'))
      await vi.advanceTimersByTimeAsync(1000)
      expect(engineRender).not.toHaveBeenCalled()
      expect(useRenderStore.getState().byKey[renderKeyOf(b)]).toBeUndefined()
      expect(useRenderStore.getState().latest['fig1.pdf']).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('对照：防抖窗口里没换项目，到点照常发出并入库', async () => {
    const { requestRender } = await import('./renderScheduler')
    vi.useFakeTimers()
    try {
      engineRender.mockResolvedValue(ok('A-fig1'))
      requestRender(panel('pA', 'fig1.pdf'), 'defer')
      await vi.advanceTimersByTimeAsync(1000)
      expect(engineRender).toHaveBeenCalledTimes(1)
      expect(useRenderStore.getState().byKey[renderKeyOf(b)].manifest?.stem).toBe('A-fig1')
    } finally {
      vi.useRealTimers()
    }
  })

  it('对照：没换项目时挂起的失败照常落成错误块', async () => {
    const { EngineError } = await import('@/lib/api')
    const a = holdNextRender()
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()
    a.reject(new EngineError('炸了', '', 'script_error', ''))
    await pendingA
    expect(useRenderStore.getState().byKey[renderKeyOf(b)].status).toBe('error')
  })
})

describe('后台（neighbour）补渲染的意图与暂缓（会话池自适应上限）', () => {
  it('前台请求不带 purpose 字段（老请求逐字节不变）；neighbour 才带', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(engineRender.mock.calls[0][2]).not.toHaveProperty('purpose')
    await useRenderStore.getState().render('Fig2.pdf', [], undefined, undefined, undefined, undefined, 'neighbour')
    expect(engineRender.mock.calls[1][2]).toMatchObject({ purpose: 'neighbour' })
  })

  it('后端暂缓：不是失败——无错误块、状态回 idle、wantPatches 清掉并记下 deferredPatches', async () => {
    const { EngineError } = await import('@/lib/api')
    const { RENDER_DEFERRED_CODE } = await import('@/lib/renderPurpose')
    engineRender.mockRejectedValue(new EngineError('池已满', '', RENDER_DEFERRED_CODE, ''))
    const p = panel('a', 'Fig1.pdf')
    useRenderStore.getState().patch(renderKeyOf(p), { fileId: 'Fig1.pdf', wantPatches: '[]' })
    await useRenderStore.getState().render('Fig1.pdf', [], undefined, undefined, undefined, undefined, 'neighbour')
    const entry = useRenderStore.getState().get(renderKeyOf(p))
    expect(entry.status).toBe('idle')
    expect(entry.error).toBeNull()
    expect(entry.code).toBe('')
    expect(entry.wantPatches).toBeNull()
    expect(entry.deferredPatches).toBe('[]')
  })

  it('暂缓时有过一版：仍是 ready，旧画面（manifest / svg）原样保留', async () => {
    const { EngineError } = await import('@/lib/api')
    const { RENDER_DEFERRED_CODE } = await import('@/lib/renderPurpose')
    const p = panel('a', 'Fig1.pdf')
    engineRender.mockResolvedValueOnce({ rev: 1, manifest: manifest('Fig1'), svg: '<svg>old</svg>' })
    await useRenderStore.getState().render('Fig1.pdf', [])
    engineRender.mockRejectedValueOnce(new EngineError('池已满', '', RENDER_DEFERRED_CODE, ''))
    await useRenderStore.getState().render('Fig1.pdf', [], undefined, undefined, undefined, undefined, 'neighbour')
    const entry = useRenderStore.getState().get(renderKeyOf(p))
    expect(entry.status).toBe('ready')
    expect(entry.svg).toContain('old')
    expect(entry.manifest).not.toBeNull()
  })

  it('暂缓之后的成功渲染清掉 deferredPatches', async () => {
    const { EngineError } = await import('@/lib/api')
    const { RENDER_DEFERRED_CODE } = await import('@/lib/renderPurpose')
    const p = panel('a', 'Fig1.pdf')
    engineRender.mockRejectedValueOnce(new EngineError('池已满', '', RENDER_DEFERRED_CODE, ''))
    await useRenderStore.getState().render('Fig1.pdf', [], undefined, undefined, undefined, undefined, 'neighbour')
    engineRender.mockResolvedValueOnce({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(useRenderStore.getState().get(renderKeyOf(p)).deferredPatches).toBeNull()
  })
})
