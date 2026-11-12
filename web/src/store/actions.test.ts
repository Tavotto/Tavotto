import { formatMessage, literal } from '@/i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject } from '@/types/document'
import type { ArrowObject, PanelChartState, PanelObject, ShapeObject, TextObject } from '@/types/document'
import { adoptComputedFrame, duplicateSelected, restoreLayoutVersion, restorePanelOverrides, setOverride } from './actions'
import { useWorkspaceStore } from './workspace'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { renderKeyOf, exactPanelRender, useRenderStore } from './renderStore'
import type { ComputedRenderFrame } from '@/lib/chartFrame'
import chartVectors from '../../../tests/golden/chart_model_vectors.json'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: unknown) => engineRender(id, patches, opts),
}))

/**
 * duplicateSelected 曾经在 Immer recipe 内部对草稿 Proxy 调 structuredClone，
 * 每次都抛 DataCloneError——⌘D / 右键复制 / 检查器复制三个入口全部静默失效。
 * 这批用例守住「克隆在 recipe 外面做」这条修复。
 */

const text = (id: string, partial: Partial<TextObject> = {}): TextObject => ({
  id, type: 'text', text: '文字', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 10, y: 20, w: 20, h: 8, ...partial,
})

const arrow = (id: string, partial: Partial<ArrowObject> = {}): ArrowObject => ({
  id, type: 'arrow', x: 10, y: 20, w: 30, h: 10,
  start: { rx: 0, ry: 1 }, end: { rx: 1, ry: 0 },
  strokePt: 1, color: '#1B1B18', head: 'end', ...partial,
})

const shape = (id: string, partial: Partial<ShapeObject> = {}): ShapeObject => ({
  id, type: 'shape', shape: 'rect', x: 10, y: 20, w: 30, h: 20,
  strokePt: 1, color: '#1B1B18', fill: null, ...partial,
})

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()

const reset = async () => {
  localStorage.clear()
  useSelectionStore.getState().clear()
  await s().switchDocument(emptyProject(), 'd_test')
}

/** 放入对象并选中它们（放入本身占一条历史，与复制那条分开计） */
const seed = (...objects: (TextObject | ArrowObject | ShapeObject)[]) => {
  s().commit(literal('放入对象'), (d) => {
    d.objects.push(...objects)
  })
  useSelectionStore.getState().set(objects.map((o) => o.id))
}

describe('duplicateSelected', () => {
  beforeEach(reset)

  for (const [kind, make] of [
    ['箭头', arrow],
    ['文字', text],
    ['形状', shape],
  ] as const) {
    it(`${kind}：不抛异常、真的多出一个对象、副本 +4mm 且换了 id`, () => {
      seed(make('o1'))
      expect(() => duplicateSelected()).not.toThrow()

      const objects = s().doc.objects
      expect(objects).toHaveLength(2)
      const [src, copy] = objects
      expect(copy.id).not.toBe(src.id)
      expect(copy.type).toBe(src.type)
      expect(copy.x).toBe(src.x + 4)
      expect(copy.y).toBe(src.y + 4)
      // 选区跟随副本，接着拖动/删除动的是新对象
      expect(useSelectionStore.getState().ids).toEqual([copy.id])
    })
  }

  it('成组对象一起复制：副本共享一个新 groupId，不与原件粘连', () => {
    seed(text('t1', { groupId: 'g1' }), text('t2', { groupId: 'g1' }))
    duplicateSelected()

    const objects = s().doc.objects
    expect(objects).toHaveLength(4)
    const copies = objects.slice(2)
    expect(copies[0].groupId).toBeTruthy()
    expect(copies[0].groupId).toBe(copies[1].groupId)
    expect(copies[0].groupId).not.toBe('g1')
  })

  it('一次复制 = 一条历史，undo 一次整体撤销', () => {
    seed(text('t1'), arrow('a1'))
    const before = s().past.length

    duplicateSelected()
    expect(s().past).toHaveLength(before + 1)
    expect(formatMessage(s().past.at(-1)!.label)).toBe('复制对象')
    expect(s().doc.objects).toHaveLength(4)

    expect(formatMessage(s().undo())).toBe('复制对象')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['t1', 'a1'])
  })

  // 2026-10-21：单图编辑里也能创建副本（取消 Codex #833 的「快编不落」）；副本是图 = 第二张图，由模式订阅切到排版
  it('单图编辑里照常创建副本：加对象、一条历史', () => {
    seed(text('t1'))
    useWorkspaceStore.getState().enterFastEdit('t1')
    try {
      const before = s().past.length
      duplicateSelected()
      expect(s().doc.objects).toHaveLength(2)
      expect(s().past).toHaveLength(before + 1)
    } finally {
      useWorkspaceStore.getState().clear()
    }
  })

  it('没有选中对象时什么都不做', () => {
    seed(text('t1'))
    useSelectionStore.getState().clear()
    const before = s().past.length

    duplicateSelected()
    expect(s().doc.objects).toHaveLength(1)
    expect(s().past).toHaveLength(before)
  })
})

/**
 * adoptComputedFrame（N5 第 3 步）：/2 渲染的外框在**一条历史**里采用进文档；
 * 缺权威不采用；undo 整体回 /1、redo 回 F；同图新响应次序颠倒时跟 exact 版走。
 */
describe('adoptComputedFrame', () => {
  const PT_MM = 25.4 / 72
  /** F 外框：S viewport 100×100pt，外框 [-21,-1,122,109]pt（原型同一向量） */
  const FRAME_A: ComputedRenderFrame = {
    version: 1, source: 'computed-svg', source_viewport_pt: [100, 100],
    rect_pt: [-21, -1, 122, 109], edit_basis: 'source-viewport-pt',
  }
  /** 另一版渲染的 F 外框（竞态用例的 exact 版） */
  const FRAME_B: ComputedRenderFrame = {
    version: 1, source: 'computed-svg', source_viewport_pt: [100, 100],
    rect_pt: [-3, -2, 104, 103], edit_basis: 'source-viewport-pt',
  }

  const chartOf = (): PanelChartState => {
    const v = chartVectors.vectors.find((v) => v.expect === 'ok')!
    return structuredClone({ model: v.model, binding: v.binding }) as unknown as PanelChartState
  }

  /** /2 诞生形状：外框暂与 S viewport 重合（originPt 0,0），native 是 S 尺寸 */
  const chartPanel = (id: string, withFrame = true): PanelObject => ({
    id, type: 'panel', fileId: 'chart.svg', fileKind: 'svg', script: null,
    x: 10, y: 20, w: 40, h: 30, nativeW: 100 * PT_MM, nativeH: 100 * PT_MM,
    overrides: [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f00' }],
    chart: chartOf(),
    ...(withFrame ? { chartFrame: { version: 1 as const, renderProfile: 'native-manifest/2' as const, originPt: [0, 0] as [number, number] } } : {}),
  })

  const chartResponse = (revision: string, frame: ComputedRenderFrame) => ({
    rev: 1, chart_revision: revision,
    manifest: { stem: revision, size_mm: [frame.rect_pt[2] * PT_MM, frame.rect_pt[3] * PT_MM], elements: [], render_frame: frame },
    svg: `<svg id="${revision}"/>`,
  })

  /** 照 renderScheduler 的 chart 调用形状发渲染（返回 promise，测试自己 await） */
  const fireRender = (p: PanelObject, response: unknown, profile?: 'native-manifest/2') => {
    engineRender.mockImplementationOnce(() => Promise.resolve(response))
    return useRenderStore.getState().render(
      p.fileId, p.overrides, undefined, 'immediate', undefined, undefined,
      'foreground', p.chart, undefined, undefined, profile ?? p.chartFrame?.renderProfile,
    )
  }

  const panelInDoc = () => s().doc.objects[0] as PanelObject

  beforeEach(async () => {
    engineRender.mockReset()
    useRenderStore.getState().clear()
    localStorage.clear()
    await s().switchDocument(emptyProject(), 'd_adopt')
  })

  it('/2 首渲采用：originPt、nativeW/H 与页面落位同一条历史', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p')) })
    const p = panelInDoc()
    await fireRender(p, chartResponse('r1', FRAME_A))
    expect(exactPanelRender(useRenderStore.getState(), p)).not.toBeNull()

    // 外框换之前，S 点 (20,30)pt 在页面上的落位（诞生形状外框 = S viewport）
    const pageOf = (o: PanelObject, frame: readonly number[], sx: number, sy: number) => [
      o.x + ((sx - frame[0]) / frame[2]) * o.w,
      o.y + ((sy - frame[1]) / frame[3]) * o.h,
    ]
    const beforePage = pageOf(p, [0, 0, 100, 100], 20, 30)

    const historyBefore = s().past.length
    expect(adoptComputedFrame('p')).toBe(true)
    const o = panelInDoc()
    expect(o.chartFrame).toEqual({ version: 1, renderProfile: 'native-manifest/2', originPt: [-21, -1] })
    expect([o.nativeW, o.nativeH]).toEqual([122 * PT_MM, 109 * PT_MM])
    // 外框换了、内容不动：同一个 S 点落位前后重合
    const afterPage = pageOf(o, FRAME_A.rect_pt, 20, 30)
    expect(afterPage[0]).toBeCloseTo(beforePage[0], 9)
    expect(afterPage[1]).toBeCloseTo(beforePage[1], 9)
    // 一条历史
    expect(s().past.length).toBe(historyBefore + 1)
  })

  it('undo 整体回 /1（chartFrame 字段删除）、redo 回 F', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p', false)) })
    const p = panelInDoc()
    // 面板还没有 chartFrame（/1 身份的键）：/2 声明已随这份响应回来（拟真后端
    // 在 manifest 里带了 render_frame），采用后才有 /2 的持久形状
    await fireRender(p, chartResponse('r1', FRAME_A))
    expect(exactPanelRender(useRenderStore.getState(), p)).not.toBeNull()

    expect(adoptComputedFrame('p')).toBe(true)
    expect(panelInDoc().chartFrame).not.toBeUndefined()

    s().undo()
    const undone = panelInDoc()
    expect(undone.chartFrame).toBeUndefined()
    expect([undone.nativeW, undone.nativeH]).toEqual([100 * PT_MM, 100 * PT_MM])
    expect([undone.w, undone.h]).toEqual([40, 30])

    s().redo()
    const redone = panelInDoc()
    expect(redone.chartFrame).toEqual({ version: 1, renderProfile: 'native-manifest/2', originPt: [-21, -1] })
    expect([redone.nativeW, redone.nativeH]).toEqual([122 * PT_MM, 109 * PT_MM])
  })

  it('渲染未回不采用：面板一字不动、不进历史', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p')) })
    const before = structuredClone(panelInDoc())
    const historyBefore = s().past.length
    // 还在渲染：条目存在但没有 manifest / lastPatches
    useRenderStore.getState().patch(renderKeyOf(panelInDoc()), { status: 'rendering' })
    expect(adoptComputedFrame('p')).toBe(false)
    expect(panelInDoc()).toEqual(before)
    expect(s().past.length).toBe(historyBefore)

    // 完全没有渲染态
    useRenderStore.getState().clear()
    expect(adoptComputedFrame('p')).toBe(false)
    expect(panelInDoc()).toEqual(before)
    expect(s().past.length).toBe(historyBefore)
  })

  it('错配拒绝：overrides 变了或 chartRequest 对不上都不采用', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p')) })
    const p = panelInDoc()
    await fireRender(p, chartResponse('r1', FRAME_A))
    const untouched = structuredClone(panelInDoc())
    const historyBefore = s().past.length

    // overrides 变了：这份渲染不再是这个变体的 exact（这次改值本身是合法编辑，
    // 要断言的是采用没有发生：外框落位一字未动）
    s().commit(literal('改值'), (d) => {
      (d.objects[0] as PanelObject).overrides.push({ gid: 'axes_0.lines_0', prop: 'linewidth', value: 3 })
    })
    expect(adoptComputedFrame('p')).toBe(false)
    expect(panelInDoc().chartFrame).toEqual(untouched.chartFrame)
    expect([panelInDoc().x, panelInDoc().y, panelInDoc().w, panelInDoc().h, panelInDoc().nativeW, panelInDoc().nativeH])
      .toEqual([untouched.x, untouched.y, untouched.w, untouched.h, untouched.nativeW, untouched.nativeH])
    expect(s().past.length).toBe(historyBefore + 1)

    // 新变体画好了，但渲染态被另一张计算图占据：chartRequest 对不上，exact 拒绝
    await fireRender(panelInDoc(), chartResponse('r2', FRAME_A))
    const edited = structuredClone(panelInDoc())
    const historyAfterEdit = s().past.length
    useRenderStore.getState().patch(renderKeyOf(panelInDoc()), {
      chartRequest: JSON.stringify({ chart: { foreign: true } }),
    })
    expect(adoptComputedFrame('p')).toBe(false)
    expect(panelInDoc()).toEqual(edited)
    expect(s().past.length).toBe(historyAfterEdit)
  })

  it('同图新响应次序颠倒：originPt 跟 exact 版走，不跟迟到旧版', async () => {
    const original = chartPanel('p')
    s().commit(literal('放入对象'), (d) => { d.objects.push(structuredClone(original)) })
    let finishA!: (v: unknown) => void
    engineRender.mockImplementationOnce(() => new Promise((r) => { finishA = r }))
    const stale = useRenderStore.getState().render(
      'chart.svg', original.overrides, undefined, 'immediate', undefined, undefined,
      'foreground', original.chart, undefined, undefined, 'native-manifest/2',
    )
    // 用户改了值 → 新变体；它的响应先回（FRAME_B）
    s().commit(literal('改值'), (d) => {
      (d.objects[0] as PanelObject).overrides.push({ gid: 'axes_0.lines_0', prop: 'linewidth', value: 3 })
    })
    await fireRender(panelInDoc(), chartResponse('r2', FRAME_B))
    // 旧响应迟到：只落自己的键，不挪 exact
    finishA(chartResponse('r1', FRAME_A))
    await stale

    expect(adoptComputedFrame('p')).toBe(true)
    expect(panelInDoc().chartFrame).toEqual({ version: 1, renderProfile: 'native-manifest/2', originPt: FRAME_B.rect_pt.slice(0, 2) })
    expect([panelInDoc().nativeW, panelInDoc().nativeH]).toEqual([FRAME_B.rect_pt[2] * PT_MM, FRAME_B.rect_pt[3] * PT_MM])
  })

  it('版本恢复往返：chartFrame 随 restoreLayoutVersion 整份走', () => {
    const two = chartPanel('p', false)
    two.chartFrame = { version: 1, renderProfile: 'native-manifest/2', originPt: [-21, -1] }
    const version = {
      schema: 2, name: 'layout', page: { w: 150, h: 100 }, guides: [],
      objects: [structuredClone(two)],
    } as unknown as Parameters<typeof restoreLayoutVersion>[1]
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p', false)) })
    restoreLayoutVersion(literal('恢复版本'), version)
    expect((s().doc.objects[0] as PanelObject).chartFrame).toEqual(two.chartFrame)
    s().undo()
    expect((s().doc.objects[0] as PanelObject).chartFrame).toBeUndefined()
  })

  /** N5 第 4 步：唯一 F→S 编辑适配（setOverride 写入口）。 */
  it('setOverride 把 F 空间的分数编辑换回 S 再落 overrides', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p')) })
    const p = panelInDoc()
    await fireRender(p, chartResponse('r1', FRAME_A))
    expect(exactPanelRender(useRenderStore.getState(), p)).not.toBeNull()
    // 显示帧 F = [-21,-1,122,109]pt：F 分数 (0.5,0.5) 的物理点在 S viewport 里是
    // ((0.5*122-21)/100, (0.5*109-1)/100) = (0.4, 0.535)
    setOverride('p', 'axes_0.title', 'pos_frac', [0.5, 0.5], true)
    const written = panelInDoc().overrides.find((o) => o.prop === 'pos_frac')
    const [wx, wy] = written?.value as [number, number]
    expect(wx).toBeCloseTo(0.4, 12)
    expect(wy).toBeCloseTo(0.535, 12)
  })

  it('缺精确配对帧时 /2 编辑零正文：不写、不进历史', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p')) })
    const before = structuredClone(panelInDoc())
    const historyBefore = s().past.length
    // 还没有任何渲染态
    setOverride('p', 'axes_0.title', 'pos_frac', [0.5, 0.5], true)
    expect(panelInDoc()).toEqual(before)
    expect(s().past.length).toBe(historyBefore)
  })

  it('/1 面板与整份恢复不走适配器：值原样落盘', async () => {
    s().commit(literal('放入对象'), (d) => { d.objects.push(chartPanel('p', false)) })
    setOverride('p', 'axes_0.title', 'pos_frac', [0.5, 0.5], true)
    expect(panelInDoc().overrides.find((o) => o.prop === 'pos_frac')?.value).toEqual([0.5, 0.5])

    // /2 面板整份恢复：列表本来就是 S 原序，不过 adapter
    s().commit(literal('换成 /2'), (d) => {
      (d.objects[0] as PanelObject).chartFrame = { version: 1, renderProfile: 'native-manifest/2', originPt: [-21, -1] }
    })
    const restored = [{ gid: 'axes_0.title', prop: 'pos_frac', value: [0.4, 0.535] }]
    restorePanelOverrides('p', literal('恢复修改'), restored)
    expect(panelInDoc().overrides).toEqual(restored)
  })
})
