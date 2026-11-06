/**
 * 统一 ExportRequest 的构造（ADR 0031）。
 *
 * 三件事各一组：`scope=original` 里**没有布局**、PPI 只在位图时有意义、
 * 快照指纹量的是"会不会出来另一个文件"。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  buildExportRequest,
  exportTargetKey,
  defaultScope,
  epsAvailability,
  filenameProblem,
  FORMATS,
  originalAvailability,
  pixelPreview,
  RASTER_FORMATS,
  snapshotRevision,
  VECTOR_FORMATS,
  type ExportRequestInput,
} from './exportRequest'
import { useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { emptyProject, type FigureDocument, type PanelObject } from '@/types/document'
import { literal } from '@/i18n'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'
import type { PanelChartState } from '@/types/document'
import { exportOffersOf } from './exportRoute'

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
}

function inputOf(over: Partial<ExportRequestInput> = {}): ExportRequestInput {
  const doc = useDocumentStore.getState().doc
  return {
    scope: 'canvas',
    formats: ['pdf', 'png'],
    filename: 'Fig 1',
    ppi: 600,
    documentId: 'd1',
    doc,
    ...over,
  }
}

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_req')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 180, h: 120 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
})

describe('计算图表导出快照', () => {
  function chartPanel(): PanelObject {
    const v = chartVectors.vectors.find(v => v.expect === 'ok')!
    const chart = structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
    return { ...panel, fileId: 'chart.svg', fileKind: 'svg', chart }
  }

  it('后端 EPS convert offer 保留真实计算场景请求、文件名和覆盖目标身份', () => {
    const p = chartPanel()
    const route = { scope: 'original' as const, formats: ['eps', 'svg'], panels: {
      [p.fileId]: { source: 'declarative' as const, embeds_disk_bitmap: false,
        formats: { eps: { how: 'convert' as const }, svg: { how: 'convert' as const } },
        offers: { eps: { how: 'convert' as const } } },
    } }
    const input = inputOf({ scope: 'original', figureId: p.fileId, panel: p, formats: ['eps', 'svg'] })
    expect(buildExportRequest(input).request.formats).toEqual(['svg'])
    const built = buildExportRequest({ ...input, route })
    expect(built.request.formats).toEqual(['eps', 'svg'])
    expect(built.names).toEqual(['Fig 1.eps', 'Fig 1.svg'])
    expect(built.request.original?.chart).toEqual(p.chart)
    expect(built.request.original?.chart_render_profile).toBe('native-manifest/1')
    expect(built.request.ppi).toBeNull()
    expect(exportTargetKey({ ...input, route })).not.toBe(exportTargetKey(input))
    expect(buildExportRequest({ ...input, scope: 'canvas', route }).request.formats).toEqual(['svg'])
    const offers = exportOffersOf(route)
    const duringRefresh = buildExportRequest({ ...input, route: null, offers })
    expect(duringRefresh).toEqual(built)
    expect(exportTargetKey({ ...input, offers })).toBe(exportTargetKey({ ...input, route }))
    expect(duringRefresh.request).not.toHaveProperty('offers')
    expect(buildExportRequest({ ...input, offers: undefined })).toEqual(buildExportRequest(input))
    expect(buildExportRequest({ ...input, scope: 'canvas', offers }).request.formats).toEqual(['svg'])
    expect(epsAvailability('original', 'another.svg', null, false, offers).ok).toBe(false)
  })

  it.each(['original', 'canvas', 'merge', 'annotations'] as const)('%s 深拷贝INPUT引用，来源改变必然改变导出身份', (kind) => {
    const p = chartPanel()
    p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: p.chart!.model.scene_digest }
    const doc = { ...useDocumentStore.getState().doc, objects: [p] }
    const input = inputOf({ scope: kind === 'canvas' ? 'canvas' : 'original', figureId: p.fileId, panel: p, doc,
      ...(kind === 'merge' ? { mergePages: [{ figureId: p.fileId, panel: p, spec: null }] } : {}),
      ...(kind === 'annotations' ? { annotations: [{ id: 'ann', type: 'text', text: 'annotation', sizePt: 9,
        bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 10, h: 5 }] } : {}),
    })
    const built = buildExportRequest(input)
    const source = kind === 'canvas' ? built.request.canvas?.objects[0]
      : kind === 'merge' ? built.request.merge_pdf?.pages[0] : built.request.original
    expect(source).toHaveProperty('chart_source', p.chartSource)
    expect((source as { chart_source: unknown }).chart_source).not.toBe(p.chartSource)
    const original = JSON.stringify(built.request)
    p.chartSource.bytesSha256 = 'b'.repeat(64)
    expect(JSON.stringify(built.request)).toBe(original)
    expect(buildExportRequest(input).revision).not.toBe(built.revision)
  })

  it('删原SVG但有附件的chart可以进入真实后端预检；无ref旧行为仍拒绝', () => {
    const p = chartPanel()
    useAssetStore.setState({ byId: {} })
    expect(originalAvailability(p.fileId, { panel: p }).ok).toBe(false)
    p.chartSource = { schema: 1, sourceId: p.fileId, bytesSha256: 'a'.repeat(64), sizeBytes: 100,
      sceneDigest: p.chart!.model.scene_digest }
    expect(originalAvailability(p.fileId, { panel: p }).ok).toBe(true)
    p.chartSource = null as unknown as NonNullable<PanelObject['chartSource']>
    expect(buildExportRequest(inputOf({ scope: 'original', figureId: p.fileId, panel: p })).request.original)
      .toHaveProperty('chart_source', null)
  })

  it.each(['original', 'canvas'] as const)('%s 保留模型/绑定，且后续编辑不改变已构造快照', (scope) => {
    const p = chartPanel()
    const doc = { ...useDocumentStore.getState().doc, objects: [p] }
    const input = inputOf({ scope, figureId: p.fileId, panel: p, doc })
    const built = buildExportRequest(input)
    const item = built.request.canvas?.objects[0]
    const state = scope === 'original' ? built.request.original?.chart : item?.type === 'panel' ? item.chart : undefined
    expect(state).toEqual(p.chart)
    expect(state).not.toBe(p.chart)
    const source = scope === 'original' ? built.request.original : item
    expect(source).toHaveProperty('chart_render_profile', 'native-manifest/1')
    const before = JSON.stringify(built.request)
    p.chart!.model.axes[0].xlim[1] += 10
    expect(buildExportRequest(input).revision).not.toBe(built.revision)
    expect(JSON.stringify(built.request)).toBe(before)
  })

  it('多页同文件的不同计算版本分别携带，且进入导出指纹', () => {
    const a = chartPanel()
    const b = chartPanel()
    b.chart!.model.axes[0].ylim[1] += 1
    const pages = [a, b].map(p => ({ figureId: p.fileId, panel: p, spec: null }))
    const input = inputOf({ scope: 'original', formats: ['pdf'], mergePages: pages })
    const built = buildExportRequest(input)
    expect(built.request.merge_pdf!.pages.map(p => p.chart)).toEqual([a.chart, b.chart])
    expect(built.request.merge_pdf!.pages.map(p => p.chart_render_profile)).toEqual(['native-manifest/1', 'native-manifest/1'])
    expect(buildExportRequest({ ...input, mergePages: [...pages].reverse() }).revision).not.toBe(built.revision)
  })

  it('缺省和不匹配面板不添加 chart 字段', () => {
    const ordinary = buildExportRequest(inputOf()).request.canvas!.objects[0]
    expect(ordinary).not.toHaveProperty('chart')
    expect(ordinary).not.toHaveProperty('chart_render_profile')
    const p = chartPanel()
    const other = buildExportRequest(inputOf({ scope: 'original', panel: p, figureId: 'other.svg' }))
    expect(other.request.original).not.toHaveProperty('chart')
    expect(other.request.original).not.toHaveProperty('chart_render_profile')
  })

  it.each(['original', 'canvas', 'merge'] as const)('%s 冻结原序完整edits及identity，指纹不合并不同顺序', (scope) => {
    const p = chartPanel()
    p.overrides = [
      { gid: 'axes_0.lines_0', prop: 'color', value: 'red', identity: 'l1:0123456789abcdef' },
      { gid: 'axes_0.lines_0', prop: 'linewidth', value: 5 },
      { gid: 'axes_0.lines_0', prop: 'color', value: 'blue' },
    ]
    const before = structuredClone(p.overrides)
    const doc = { ...useDocumentStore.getState().doc, objects: [p] }
    const input = inputOf({ scope: scope === 'canvas' ? 'canvas' : 'original', formats: ['pdf'], figureId: p.fileId, panel: p, doc,
      ...(scope === 'merge' ? { mergePages: [{ figureId: p.fileId, panel: p, spec: null }] } : {}) })
    const built = buildExportRequest(input)
    const source = scope === 'canvas' ? built.request.canvas!.objects[0] : scope === 'merge' ? built.request.merge_pdf!.pages[0] : built.request.original!
    expect(source).toHaveProperty('overrides', before)
    expect(source).toHaveProperty('chart_render_profile', 'native-manifest/1')
    p.overrides[0].value = 'green'
    expect(source).toHaveProperty('overrides', before)
    expect(buildExportRequest(input).revision).not.toBe(built.revision)
    p.overrides = before.slice(0, 2)
    const first = buildExportRequest(input).revision
    p.overrides = [...p.overrides].reverse()
    expect(buildExportRequest(input).revision).not.toBe(first)
  })
})

describe('scope', () => {
  it('默认跟着工作流走', () => {
    expect(defaultScope('fast_edit')).toBe('original')
    expect(defaultScope('layout')).toBe('canvas')
  })

  it('`original` 段里没有 x/y/w/h；尺寸来自 spec 而不是画布上的落位', () => {
    // **面板在画布上被缩到 40 × 30，而它自己是 80 × 60**：两个数字必须不同，
    // 否则"用的是 spec 还是用的是 w/h"这个问题在这条用例里根本量不出来
    // （变异反证当场抓到过：夹具让判据恒真）
    useDocumentStore.getState().commit(literal('缩小'), (d) => {
      const p0 = d.objects[0] as PanelObject
      p0.w = 40
      p0.h = 30
    })
    const doc = useDocumentStore.getState().doc
    const p = doc.objects[0] as PanelObject
    const { request } = buildExportRequest(
      inputOf({
        doc,
        scope: 'original',
        figureId: 'Fig1.pdf',
        panel: p,
        spec: {
          figureId: 'Fig1.pdf',
          sourceKind: 'vector',
          widthMm: 80,
          heightMm: 60,
          pixelWidth: null,
          pixelHeight: null,
          dpi: null,
          dpiSource: 'unknown',
          viewportPt: null,
          transparent: null,
          origin: 'document',
          stale: false,
          fallback: false,
          ignored: ['scale'],
        },
      }),
    )
    expect(request.canvas).toBeUndefined()
    const keys = Object.keys(request.original!)
    for (const forbidden of ['x_mm', 'y_mm', 'w', 'h', 'page_w_mm', 'page_h_mm', 'crop']) {
      expect(keys, `original 段里不该有 ${forbidden}`).not.toContain(forbidden)
    }
    // 被忽略的变换要说出来：忽略而不说等于骗人
    expect(request.original!.ignored).toEqual(['scale'])
    // 图幅是 80 × 60（它自己的），不是 40 × 30（画布上的落位）
    expect(p.w).toBe(40)
    expect(request.original!.w_mm).toBe(80)
    expect(request.original!.h_mm).toBe(60)
  })

  it('画布范围发的是页面 + z 序对象，隐藏对象不发', () => {
    useDocumentStore.getState().commit(literal('藏一个'), (d) => {
      d.objects = [
        { ...panel },
        { ...panel, id: 'p2', hidden: true },
      ]
    })
    const { request } = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc }))
    expect(request.original).toBeUndefined()
    expect(request.canvas!.page_w_mm).toBe(180)
    expect(request.canvas!.objects).toHaveLength(1)
  })

  it('原图不可用时说得出**为什么**，而不是回一个笼统的 false', () => {
    expect(originalAvailability(null)).toMatchObject({ ok: false, reason: 'no_figure' })
    // 「没选」与「没得选」是两句话：项目里一张图都没有时不能让用户去"点选一张"
    expect(originalAvailability(null, { anyFigures: true })).toMatchObject({ reason: 'no_figure' })
    expect(originalAvailability(null, { anyFigures: false })).toMatchObject({
      ok: false,
      reason: 'no_figures',
    })
    expect(originalAvailability('不存在.pdf')).toMatchObject({
      ok: false,
      reason: 'unknown_figure',
    })
    expect(originalAvailability('Fig1.pdf').ok).toBe(true)
  })

  it('源文件不在素材清单里 → **不可用**（不是给一个按下去必然失败的按钮）', () => {
    // 面板还在文档里（规格取自它的 nativeW/H），但素材清单里没有这个 id：
    // 后端解析面板源的第一步就是 safe_resolve()，文件不在就 404 ——
    // 「引擎能重新画一张」这个指望在那条路上兑现不了
    useAssetStore.setState({ byId: {} } as never)
    const a = originalAvailability('Fig1.pdf')
    expect(a.spec).toBeTruthy() // 规格还在（上一次已知的那份），只是导不出来
    expect(a.ok).toBe(false)
    expect(a.reason).toBe('source_stale')
  })

  it('runtime 图：素材清单没有它——后端注册表说了算；未知不判假', () => {
    const rt: PanelObject = { ...panel, id: 'r1', fileId: 'runtime:fig.py#F', fileKind: 'runtime' } as never
    useAssetStore.setState({ byId: {} } as never)
    // 后端明确回「不认」：不可用
    useRuntimeAssetStore.setState({ assets: [], byId: { 'runtime:fig.py#F': { registered: false, checked: true } } } as never)
    expect(originalAvailability('runtime:fig.py#F', { panel: rt })).toMatchObject({ ok: false, reason: 'source_stale' })
    // 未知（清单没取到 / 失效后）：不判 source_stale，由对话框去问
    useRuntimeAssetStore.setState({ assets: null, byId: {} } as never)
    expect(originalAvailability('runtime:fig.py#F', { panel: rt }).ok).toBe(true)
    useRuntimeAssetStore.setState({ byId: { 'runtime:fig.py#F': { registered: null, checked: false } } } as never)
    expect(originalAvailability('runtime:fig.py#F', { panel: rt }).ok).toBe(true)
    useRuntimeAssetStore.setState({ byId: { 'runtime:fig.py#F': { registered: true, checked: true } } } as never)
    expect(originalAvailability('runtime:fig.py#F', { panel: rt }).ok).toBe(true)
    // 缓存的素材清单还留着旧条目、而刚问来的结果明确「不认」：逐面板结果优先
    useRuntimeAssetStore.setState({
      assets: [{ id: 'runtime:fig.py#F' }],
      byId: { 'runtime:fig.py#F': { registered: false, checked: true } },
    } as never)
    expect(originalAvailability('runtime:fig.py#F', { panel: rt })).toMatchObject({ ok: false, reason: 'source_stale' })
  })

  it('判据是「够不够得着」而不是 `spec.stale`', () => {
    // 刚渲染过的图：manifest 还在手上，`stale` 是 false，而磁盘文件可能早没了。
    // 拿 `stale` 当判据的话这张图会拿到一个按下去必然失败的按钮
    useRenderStore.setState({
      byKey: {
        [renderKey('Fig1.pdf', [])]: {
          fileId: 'Fig1.pdf',
          manifest: { stem: 'Fig1', size_mm: [80, 60], elements: [] },
          status: 'ready',
        },
      },
      latest: { 'Fig1.pdf': renderKey('Fig1.pdf', []) },
      tracked: {},
      building: {},
    } as never)
    useAssetStore.setState({ byId: {} } as never)
    const a = originalAvailability('Fig1.pdf')
    expect(a.spec?.stale, '这张图的规格是新鲜的（origin=render_metadata）').toBe(false)
    expect(a.ok, '规格新鲜 ≠ 源文件够得着').toBe(false)
    expect(a.reason).toBe('source_stale')
  })
})

describe('PPI 只在位图输出时有意义', () => {
  it('只出 PDF 时 ppi 是 null，不是一个不起作用的默认值', () => {
    expect(buildExportRequest(inputOf({ formats: ['pdf'] })).request.ppi).toBeNull()
    expect(buildExportRequest(inputOf({ formats: ['png'] })).request.ppi).toBe(600)
  })

  it('超出范围的 ppi 被夹住，不发一个后端要拒绝的数', () => {
    expect(buildExportRequest(inputOf({ ppi: 999999 })).request.ppi).toBe(1200)
    expect(buildExportRequest(inputOf({ ppi: 1 })).request.ppi).toBe(36)
    expect(buildExportRequest(inputOf({ ppi: Number.NaN })).request.ppi).toBe(600)
  })
})

describe('产物检查政策（ADR 0068）', () => {
  it('不勾严格时载荷里**没有** `inspection` 键：老形状一个字节不变', () => {
    const { request } = buildExportRequest(inputOf())
    expect('inspection' in request).toBe(false)
    expect('inspection' in buildExportRequest(inputOf({ strictInspection: false })).request).toBe(
      false,
    )
  })

  it('勾了严格才带 `inspection: strict`，并带上对话框里选中的规范', () => {
    const { request } = buildExportRequest(
      inputOf({ strictInspection: true, profileId: 'lab-publication-v1' }),
    )
    expect(request.inspection).toEqual({ mode: 'strict', profile_id: 'lab-publication-v1' })
  })

  it('严格开关不进快照指纹：它改的是发不发布，不是出来的文件', () => {
    const a = buildExportRequest(inputOf()).revision
    const b = buildExportRequest(inputOf({ strictInspection: true, profileId: 'x' })).revision
    expect(a).toBe(b)
  })
})

describe('文件名', () => {
  it('顺手打上的扩展名被剥掉，非法字符当场报原因', () => {
    expect(buildExportRequest(inputOf({ filename: 'Fig 1.pdf' })).request.filename).toBe('Fig 1')
    expect(filenameProblem('Fig?1', ['pdf'])).toBe('illegal_char')
    expect(filenameProblem('Fig 1.pdf', ['pdf'])).toBeNull()
  })

  it('预览出这次会写哪几个文件', () => {
    expect(buildExportRequest(inputOf()).names).toEqual(['Fig 1.pdf', 'Fig 1.png'])
    expect(buildExportRequest(inputOf({ formats: ['png'] })).names).toEqual(['Fig 1.png'])
  })
})

describe('快照指纹', () => {
  it('量的是"会不会出来另一个文件"，不是"文档有没有被动过"', () => {
    const a = buildExportRequest(inputOf()).revision
    // 改画布名：导出结果一模一样 → 指纹不变（不然完成时会冒一句假的"被编辑过"）
    useDocumentStore.getState().renameProject('另一个名字')
    const b = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc })).revision
    expect(b).toBe(a)

    // 挪一个对象：出来的图不一样 → 指纹必须变
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[0].x = 33
    })
    const c = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc })).revision
    expect(c).not.toBe(a)
  })

  it('两个 scope 的指纹互不相干', () => {
    const doc = useDocumentStore.getState().doc as FigureDocument
    const canvas = snapshotRevision(buildExportRequest(inputOf({ doc })).request)
    const original = snapshotRevision(
      buildExportRequest(inputOf({ doc, scope: 'original', figureId: 'Fig1.pdf' })).request,
    )
    expect(original).not.toBe(canvas)
  })
})

describe('像素预览：照抄源文件才报源像素网格', () => {
  const page = { w: 180, h: 120 }
  const raster = {
    widthMm: 10.16,
    heightMm: 6.77,
    pixelWidth: 120,
    pixelHeight: 80,
    sourceKind: 'raster',
  } as never

  it('照抄源位图时报源像素网格，与 ppi 无关', () => {
    expect(pixelPreview('original', 600, page, raster, true)).toContain('120')
    expect(pixelPreview('original', 300, page, raster, true)).toContain('120')
  })

  it('**带 override 的位图会被引擎重画**：这时 ppi 说了算，不许报源像素网格', () => {
    const shown = pixelPreview('original', 600, page, raster, false)
    expect(shown, '对着一张即将被重画的图报源像素网格 = 界面与文件各说各的').not.toContain(
      '120 × 80',
    )
    // 10.16mm @ 600ppi ≈ 240px
    expect(shown).toContain('240')
  })
})

describe('EPS 与 TIFF（ADR 0046）', () => {
  it('tiff 是位图、eps 是矢量：PPI 规则对新格式同样成立', () => {
    expect(FORMATS).toEqual(['pdf', 'png', 'eps', 'tiff', 'svg'])
    expect([...RASTER_FORMATS, ...VECTOR_FORMATS].sort()).toEqual([...FORMATS].sort())
    expect(buildExportRequest(inputOf({ formats: ['tiff'] })).request.ppi).toBe(600)
    expect(buildExportRequest(inputOf({ formats: ['pdf', 'tiff'], ppi: 300 })).request.ppi).toBe(
      300,
    )
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
    } as never)
    const epsOnly = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'Fig1.pdf', formats: ['eps'] }),
    )
    expect(epsOnly.request.ppi, 'EPS 是矢量，PPI 对它没有意义').toBeNull()
    expect(epsOnly.names).toEqual(['Fig 1.eps'])
  })

  it('结果顺序来自 FORMATS，不是勾选顺序；四个格式的文件名都能预览', () => {
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
    } as never)
    const built = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'Fig1.pdf', formats: ['tiff', 'eps', 'png', 'pdf'] }),
    )
    expect(built.request.formats).toEqual(['pdf', 'png', 'eps', 'tiff'])
    expect(built.names).toEqual(['Fig 1.pdf', 'Fig 1.png', 'Fig 1.eps', 'Fig 1.tiff'])
    expect(filenameProblem('Fig 1.tif', ['tiff']), '`.tif` 别名也被剥掉').toBeNull()
    expect(buildExportRequest(inputOf({ filename: 'Fig 1.tiff', formats: ['tiff'] })).request.filename).toBe('Fig 1')
  })

  it('EPS 只在「原图 + 有脚本」时可用，不可用时说得出原因', () => {
    expect(epsAvailability('canvas', 'Fig1.pdf')).toEqual({ ok: false, reason: 'canvas_scope' })
    expect(epsAvailability('original', null)).toEqual({ ok: false, reason: 'no_script' })
    // 素材清单里没有 script = 注册表没有这张图的脚本
    expect(epsAvailability('original', 'Fig1.pdf')).toEqual({ ok: false, reason: 'no_script' })
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
    } as never)
    expect(epsAvailability('original', 'Fig1.pdf')).toEqual({ ok: true, reason: 'none' })
    // runtime 素材天生有脚本
    expect(epsAvailability('original', 'runtime:abc')).toEqual({ ok: true, reason: 'none' })
  })

  it('单图档（没有范围选择）静默退到画布时，EPS 的原因不再指向不存在的「每张图单独导出」', () => {
    // 没有脚本的图（静态文件 / 原文件已删）：先说「没有脚本」，不叫用户去运行一个不存在的脚本
    expect(epsAvailability('canvas', 'Fig1.pdf', null, true)).toEqual({ ok: false, reason: 'no_script' })
    expect(epsAvailability('canvas', null, null, true)).toEqual({ ok: false, reason: 'no_script' })
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } },
    } as never)
    expect(epsAvailability('canvas', 'Fig1.pdf', null, true)).toEqual({ ok: false, reason: 'figure_not_ready' })
    expect(epsAvailability('canvas', 'runtime:abc', null, true)).toEqual({ ok: false, reason: 'figure_not_ready' })
    // 多图档的画布范围仍是「选每张图单独导出」那句
    expect(epsAvailability('canvas', 'Fig1.pdf', null, false)).toEqual({ ok: false, reason: 'canvas_scope' })
    // 单图档里原图口径可用就照常给 EPS（runtime 素材天生有脚本）
    expect(epsAvailability('original', 'runtime:abc', null, true)).toEqual({ ok: true, reason: 'none' })
  })

  it('SVG 素材与 PDF 同一判据：有脚本可用（字节绑定由后端逐项判，不预先禁用）、没脚本 no_script', () => {
    useAssetStore.setState({
      byId: {
        'S1.svg': { id: 'S1.svg', mtime: 1, kind: 'svg', script: 's1.py' },
        'S2.svg': { id: 'S2.svg', mtime: 1, kind: 'svg' },
      },
    } as never)
    expect(epsAvailability('original', 'S1.svg')).toEqual({ ok: true, reason: 'none' })
    // 前端拿不到字节绑定的事实：不预先禁用，请求照带 eps，由后端逐项失败并给真实原因（#1185）
    expect(
      buildExportRequest(inputOf({ scope: 'original', figureId: 'S1.svg', formats: ['svg', 'eps'] })).request.formats,
    ).toEqual(['eps', 'svg'])
    expect(epsAvailability('original', 'S2.svg')).toEqual({ ok: false, reason: 'no_script' })
  })

  it('EPS 不可用时请求里**不带**它（后端会逐项报失败，但界面已经说过一遍了）', () => {
    const canvas = buildExportRequest(inputOf({ scope: 'canvas', formats: ['pdf', 'eps'] }))
    expect(canvas.request.formats).toEqual(['pdf'])
    expect(canvas.names).toEqual(['Fig 1.pdf'])
    // 只勾了 EPS 又在画布范围：什么都发不出去，names 为空（按钮据此变灰）
    const nothing = buildExportRequest(inputOf({ scope: 'canvas', formats: ['eps'] }))
    expect(nothing.request.formats).toEqual([])
    expect(nothing.names).toEqual([])
    // 原图 + 没脚本：同样不带
    const noScript = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'Fig1.pdf', formats: ['pdf', 'eps', 'tiff'] }),
    )
    expect(noScript.request.formats).toEqual(['pdf', 'tiff'])
  })
})


describe('SVG（X03）', () => {
  it.each(['canvas', 'original'] as const)('SVG is vector in %s scope with no PPI', (scope) => {
    const built = buildExportRequest(inputOf({ scope, figureId: 'Fig1.pdf', formats: ['svg'], filename: 'Fig 1.SVG' }))
    expect(built.request.formats).toEqual(['svg'])
    expect(built.request.ppi).toBeNull()
    expect(built.request.filename).toBe('Fig 1')
    expect(built.names).toEqual(['Fig 1.svg'])
    expect(VECTOR_FORMATS).toContain('svg')
    expect(RASTER_FORMATS).not.toContain('svg')
  })

  it('appends SVG after the previous formats and keeps PPI for mixed raster output', () => {
    useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1, script: 'fig1.py' } } } as never)
    const built = buildExportRequest(inputOf({ scope: 'original', figureId: 'Fig1.pdf', formats: ['svg', 'tiff', 'eps', 'png', 'pdf', 'svg'], ppi: 300 }))
    expect(built.request.formats).toEqual(['pdf', 'png', 'eps', 'tiff', 'svg'])
    expect(built.names).toEqual(['Fig 1.pdf', 'Fig 1.png', 'Fig 1.eps', 'Fig 1.tiff', 'Fig 1.svg'])
    expect(built.request.ppi).toBe(300)
  })
})

describe('合成多页 PDF（#1370）', () => {
  const pages = [
    { figureId: 'A.pdf', panel: null, spec: null },
    { figureId: 'B.pdf', panel: null, spec: null },
    { figureId: 'A.pdf', panel: null, spec: null },
  ]

  it('mergePages 变成 merge_pdf.pages（按页序、重复保留），请求里没有 original', () => {
    const { request } = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'A.pdf', formats: ['pdf'], filename: 'all_samples', mergePages: pages }),
    )
    expect(request.original).toBeUndefined()
    expect(request.merge_pdf?.pages.map((p) => p.figure_id)).toEqual(['A.pdf', 'B.pdf', 'A.pdf'])
    expect(request.filename).toBe('all_samples')
  })

  it('不给 mergePages：与从前一样，只有 original', () => {
    const { request } = buildExportRequest(inputOf({ scope: 'original', figureId: 'A.pdf', formats: ['pdf'] }))
    expect(request.merge_pdf).toBeUndefined()
    expect(request.original?.figure_id).toBe('A.pdf')
  })

  it('页序进快照指纹：换了页序就是另一个导出', () => {
    const mk = (m: typeof pages) =>
      buildExportRequest(inputOf({ scope: 'original', figureId: 'A.pdf', formats: ['pdf'], mergePages: m })).revision
    expect(mk(pages)).not.toBe(mk([pages[1], pages[0], pages[2]]))
  })
})

describe('单图画布留白进 original.pad_mm（ADR 0161）', () => {
  it('非零留白：带 pad_mm（0.01 mm 一格）；全 0 / 缺省：没有这个键', () => {
    const withPad = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'Fig1.pdf', framePad: { l: 2.004, t: 0, r: -3.333, b: 1 } }),
    ).request
    expect(withPad.original?.pad_mm).toEqual([2, 0, -3.33, 1])
    const zero = buildExportRequest(
      inputOf({ scope: 'original', figureId: 'Fig1.pdf', framePad: { l: 0, t: 0, r: 0, b: 0 } }),
    ).request
    expect(zero.original && 'pad_mm' in zero.original).toBe(false)
    const none = buildExportRequest(inputOf({ scope: 'original', figureId: 'Fig1.pdf' })).request
    expect(none.original && 'pad_mm' in none.original).toBe(false)
  })
  it('画布口径不带留白', () => {
    const r = buildExportRequest(inputOf({ framePad: { l: 5, t: 0, r: 0, b: 0 } })).request
    expect(r.original).toBeUndefined()
  })
})
