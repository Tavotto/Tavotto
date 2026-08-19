/**
 * 假实时交互的端到端契约：**预览平面与历史平面各干各的**。
 *
 * 要钉住的事实，一条都不能松：
 *   1. 拖动期间（一百次 pointermove）matplotlib 一次都不跑；
 *   2. 松手只发一次权威渲染，用的是最后一帧预览的值；
 *   3. pointercancel 不提交、不进历史、不渲染、不留临时 transform；
 *   4. 一次拖动 = 一条历史；两次拖动 = 两条；
 *   5. undo/redo 恢复的是**正式 override 的准确值**，并触发新的权威渲染；
 *   6. 权威渲染失败时历史照样在（渲染成不成功与记不记账无关）。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { syncEngine } from '@/hooks/useEngineSync'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { flushPreviewFrame, previewSession, resetPreview } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { startArrowDrag, startAxesDrag, startElementDrag } from './interactions'

/* ------------------------------- 引擎打桩 -------------------------------- */

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* -------------------------------- 测试数据 -------------------------------- */

const textEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.2, 0.05, 0.4, 0.08],
  editable: [],
  draggable: true,
  anchor: [0.4, 0.09],
  drag_prop: 'pos_frac',
}

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图',
  bbox: [0.1, 0.1, 0.8, 0.8],
  // 子图的几何来自 position 字段（figure 占比，bottom-origin），startAxesDrag
  // 拿不到它就整个不起手
  editable: [{ prop: 'position', type: 'rect', value: [0.12, 0.11, 0.8, 0.77] }],
  draggable: false,
  resizable: true,
}

const arrowEl: ManifestElement = {
  gid: 'axes_0.arrows_3',
  role: 'arrow_patch',
  label: '箭头',
  bbox: [0.2, 0.2, 0.3, 0.2],
  editable: [],
  draggable: true,
  arrow_endpoints: [
    [0.2, 0.4],
    [0.5, 0.2],
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    axesEl,
    textEl,
    arrowEl,
  ],
}

const panelOf = (overrides: PanelObject['overrides'] = []): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides,
  }) as unknown as PanelObject

const layout = { width: mmToWorld(101.6), height: mmToWorld(76.2) }

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

/* ------------------------------- 指针事件桩 ------------------------------- */

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  clientX: number,
  clientY: number,
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

/** 拖 n 步到 (x, y)：每一步都真的发一次 pointermove + 刷一帧 */
function dragTo(x: number, y: number, steps = 100) {
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', (x * i) / steps, (y * i) / steps)
    flushPreviewFrame()
  }
}

const tf = (gid: string) =>
  document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)?.getAttribute('transform') ?? null

/* -------------------------------- 环境搭建 -------------------------------- */

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fake_realtime')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  // 画布上挂着这一版：SVG 与 manifest 同一次响应（inline_svg）
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(panelOf()) } })
  document.body.innerHTML = `<div data-element-svg="p1">${MATPLOTLIB_SVG}</div>`
  // 面板刚加进来那一条不该混进后面的历史计数
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ============================ 1. 拖动期间零后端 ============================ */

describe('pointermove 不触发 Matplotlib', () => {
  it('一百次 pointermove：engineRender 一次都没被叫到', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(120, 60, 100)
    expect(engineRender).not.toHaveBeenCalled()
    // 同步器这一轮也不该替它发（拖动没写文档，变体键没变）
    syncEngine(useDocumentStore.getState().doc.objects, 'p1')
    expect(engineRender).not.toHaveBeenCalled()
    fire('pointercancel', 120, 60)
  })

  it('拖动期间画面是真的在动（预览确实贴到 SVG 上了）', () => {
    expect(tf('axes_0.title')).toBeNull()
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(120, 60, 100)
    expect(tf('axes_0.title')).toMatch(/^translate\(/)
    fire('pointercancel', 120, 60)
  })

  it('拖动期间文档一个字节都没动（预览绝不污染正式文档）', () => {
    const before = JSON.stringify(useDocumentStore.getState().doc)
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(120, 60, 100)
    expect(JSON.stringify(useDocumentStore.getState().doc)).toBe(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    fire('pointercancel', 120, 60)
  })
})

/* ============================ 2. 松手一次定稿 ============================== */

describe('pointerup：一次权威渲染，用最后一帧的值', () => {
  it('只发一次，patch 正是最后一帧预览的位置', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(101.6 * 4, 0, 100) // 世界像素：mmToWorld(101.6) = 图宽 → dfx 走满 1
    fire('pointerup', 101.6 * 4, 0)

    expect(engineRender).toHaveBeenCalledTimes(1)
    const value = overrideOf('axes_0.title', 'pos_frac') as number[]
    // anchor.x + dfx；dfx = 拖动像素 / 内容宽
    expect(value[0]).toBeCloseTo(textEl.anchor![0] + (101.6 * 4) / layout.width, 6)
    expect(value[1]).toBeCloseTo(textEl.anchor![1], 6)
    // 发出去的 patches 就是文档里那一份
    expect(engineRender.mock.calls[0][1]).toEqual(livePanel().overrides)
  })

  it('预览挂到权威渲染追上来为止（提交后 DOM 上的位移一动不动）', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(200, 100, 50)
    const applied = tf('axes_0.title')
    fire('pointerup', 200, 100)
    expect(tf('axes_0.title')).toBe(applied)
    expect(previewSession()!.pendingCommit).not.toBeNull()
    // 等的正是提交后那一版
    expect(previewSession()!.awaitKey).toBe(renderKeyOf(livePanel()))
  })

  it('子图整体拖动、箭头整体拖动同样是「零后端 → 一次定稿」', () => {
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(40, 20, 100)
    expect(engineRender).not.toHaveBeenCalled()
    fire('pointerup', 40, 20)
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(overrideOf('axes_0', 'position')).toBeDefined()

    engineRender.mockClear()
    startArrowDrag(down(0, 0), livePanel(), arrowEl, layout, 'both')
    dragTo(30, 15, 100)
    expect(engineRender).not.toHaveBeenCalled()
    fire('pointerup', 30, 15)
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(overrideOf('axes_0.arrows_3', 'endpoints_frac')).toBeDefined()
  })

  it('子图**缩放**不做 SVG 假预览，但最终 patch 与覆盖层线框仍然正确', () => {
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'e')
    dragTo(40, 0, 30)
    // 缩放会让 matplotlib 重排（刻度、字号不跟着线性缩放），假装缩放是骗人
    expect(tf('axes_0')).toBeNull()
    // 覆盖层照常给线框
    expect(useInteractionStore.getState().elementPreview?.boxes['axes_0']).toBeDefined()
    fire('pointerup', 40, 0)
    const pos = overrideOf('axes_0', 'position') as number[]
    expect(pos).toHaveLength(4)
    expect(pos[2]).toBeGreaterThan(0.8) // 往右拖 → 变宽
    expect(engineRender).toHaveBeenCalledTimes(1)
  })
})

/* ============================== 3. 取消语义 =============================== */

describe('pointercancel', () => {
  it('不写 override、不进历史、不渲染、不留临时 transform', () => {
    const svgBefore = document.querySelector('svg')!.outerHTML
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(150, 80, 100)
    expect(tf('axes_0.title')).toMatch(/^translate\(/)

    fire('pointercancel', 150, 80)

    expect(overrideOf('axes_0.title', 'pos_frac')).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(tf('axes_0.title')).toBeNull()
    expect(document.querySelector('svg')!.outerHTML).toBe(svgBefore)
    expect(previewSession()).toBeNull()
  })

  it('子图与箭头的取消同样干净', () => {
    const svgBefore = document.querySelector('svg')!.outerHTML
    startAxesDrag(down(0, 0), livePanel(), axesEl, layout, 'move')
    dragTo(60, 30, 40)
    fire('pointercancel', 60, 30)
    expect(overrideOf('axes_0', 'position')).toBeUndefined()

    startArrowDrag(down(0, 0), livePanel(), arrowEl, layout, 'both')
    dragTo(60, 30, 40)
    fire('pointercancel', 60, 30)
    expect(overrideOf('axes_0.arrows_3', 'endpoints_frac')).toBeUndefined()

    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(document.querySelector('svg')!.outerHTML).toBe(svgBefore)
  })

  it('取消之后紧接着正常拖一次，落点正确（没有被上一轮的 base 污染）', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(300, 0, 20)
    fire('pointercancel', 300, 0)

    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(100, 0, 20)
    fire('pointerup', 100, 0)
    const value = overrideOf('axes_0.title', 'pos_frac') as number[]
    expect(value[0]).toBeCloseTo(textEl.anchor![0] + 100 / layout.width, 6)
  })
})

/* ============================== 4. 历史粒度 =============================== */

describe('历史：一次拖动一条', () => {
  it('一次拖动含一百次 pointermove，只压一条历史', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(120, 60, 100)
    fire('pointerup', 120, 60)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('两次独立拖动 = 两条独立历史，绝不被合并', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(80, 0, 30)
    fire('pointerup', 80, 0)
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(0, 60, 30)
    fire('pointerup', 0, 60)
    expect(useDocumentStore.getState().past).toHaveLength(2)
  })

  it('没真的动过（只是点了一下）不产生历史，也不渲染', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    fire('pointerup', 0, 0)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
  })
})

/* ============================ 5. 撤销 / 重做 ============================== */

describe('undo / redo 恢复正式 override 的准确值', () => {
  it('撤销回到拖动前，重做回到拖动后，两边的值都精确还原', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(160, 0, 40)
    fire('pointerup', 160, 0)
    const after = overrideOf('axes_0.title', 'pos_frac')
    expect(after).toBeDefined()

    useDocumentStore.getState().undo()
    expect(overrideOf('axes_0.title', 'pos_frac')).toBeUndefined()
    useDocumentStore.getState().redo()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual(after)
  })

  it('撤销回到一个**已经画好**的变体：不白跑一次引擎', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(160, 0, 40)
    fire('pointerup', 160, 0)
    // 这一版画回来了
    useRenderStore.getState().patch(renderKeyOf(livePanel()), {
      fileId: 'Fig1.pdf',
      manifest,
      svg: MATPLOTLIB_SVG,
      status: 'ready',
      lastPatches: JSON.stringify(livePanel().overrides),
    })

    engineRender.mockClear()
    useDocumentStore.getState().undo()
    syncEngine(useDocumentStore.getState().doc.objects, 'p1')
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('那一版不在渲染态里时，撤销/重做按**当下**的 overrides 重新渲染', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(160, 0, 40)
    fire('pointerup', 160, 0)
    const after = overrideOf('axes_0.title', 'pos_frac')

    useDocumentStore.getState().undo()
    // 编辑期每改一个值就多一条变体，prune 会把没人引用的清掉——撤销之后
    // 那一版完全可能已经不在渲染态里了
    useRenderStore.getState().clear()
    engineRender.mockClear()
    syncEngine(useDocumentStore.getState().doc.objects, 'p1')
    expect(engineRender).toHaveBeenCalledTimes(1)
    // 用的是撤销之后的 overrides，不是事件闭包里那份旧的
    expect(engineRender.mock.calls[0][1]).toEqual([])

    useDocumentStore.getState().redo()
    useRenderStore.getState().clear()
    engineRender.mockClear()
    syncEngine(useDocumentStore.getState().doc.objects, 'p1')
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(engineRender.mock.calls[0][1]).toEqual(livePanel().overrides)
    expect((engineRender.mock.calls[0][1] as { value: unknown }[])[0].value).toEqual(after)
  })

  it('同一变体在飞时不重复发（撤销→重做回到在途那一版，合并成一次）', async () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(160, 0, 40)
    fire('pointerup', 160, 0)
    expect(engineRender).toHaveBeenCalledTimes(1)

    useDocumentStore.getState().undo()
    useDocumentStore.getState().redo()
    syncEngine(useDocumentStore.getState().doc.objects, 'p1')
    // 那一版正在飞，renderStore 的 busy/queued 兜住了：不该再发一次
    expect(engineRender).toHaveBeenCalledTimes(1)
    await Promise.resolve()
  })

  it('两次拖动分别可撤销，撤一次只退一步', () => {
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(80, 0, 20)
    fire('pointerup', 80, 0)
    const first = overrideOf('axes_0.title', 'pos_frac')

    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(0, 60, 20)
    fire('pointerup', 0, 60)
    expect(overrideOf('axes_0.title', 'pos_frac')).not.toEqual(first)

    useDocumentStore.getState().undo()
    expect(overrideOf('axes_0.title', 'pos_frac')).toEqual(first)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
})

/* ============================ 6. 渲染失败 ================================= */

describe('权威渲染失败', () => {
  it('历史条目照样在，override 也在——记账与渲染成不成功无关', async () => {
    engineRender.mockRejectedValue(new Error('worker 崩了'))
    startElementDrag(down(0, 0), livePanel(), textEl, layout)
    dragTo(120, 0, 30)
    fire('pointerup', 120, 0)
    await Promise.resolve()
    await Promise.resolve()

    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(overrideOf('axes_0.title', 'pos_frac')).toBeDefined()
    // 撤销仍然能把它退回去
    useDocumentStore.getState().undo()
    expect(overrideOf('axes_0.title', 'pos_frac')).toBeUndefined()
  })
})
