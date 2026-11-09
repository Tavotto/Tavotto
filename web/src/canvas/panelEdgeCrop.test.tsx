/**
 * 多图排版里拖面板的边 = 裁剪（ADR 0161 §4）：写 `panel.crop`、整图在画布上不动、一条历史；
 * 停靠点（图边 / 内容边缘）与 ⌥ 放行；旋转 / 翻转与单图编辑里边柄维持原样。
 *
 * 版面：pa 在 (10,20) 100×75（原生同尺寸，缩放 100%）；缩放 1 时 mmToWorld(1mm) 个屏幕像素 = 1mm，
 * 吸附半径 6px ≈ 1.6mm ≈ 1.6% 图宽。ink_bbox 左缘在 0.10。
 */
import { formatMessage, literal } from '@/i18n'
import { produce } from 'immer'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest } from '@/lib/api'
import { syncPanelNativeSize } from '@/lib/panelNativeSize'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { LayoutFramePreview } from './PageEdgeResize'
import { startPanelHandleDrag } from './panelEdgeCrop'

const INK: [number, number, number, number] = [0.1, 0.2, 0.9, 0.8]

const SIZE_FIELD = { prop: 'size_mm', label: '图幅', type: 'size', value: [100, 75], min: 20, max: 300 }

const manifestOf = (ink?: [number, number, number, number], editableSize = false): Manifest => ({
  stem: 'Fig',
  size_mm: [100, 75],
  elements: [
    {
      gid: 'figure',
      role: 'figure',
      label: '整图',
      bbox: [0, 0, 1, 1],
      editable: editableSize ? [SIZE_FIELD as never] : [],
      draggable: false,
      ...(ink ? { ink_bbox: ink } : {}),
    },
  ],
})

const panel = (extra: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'pa',
    type: 'panel',
    x: 10,
    y: 20,
    w: 100,
    h: 75,
    fileId: 'pa.pdf',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 75,
    script: 'fig.py',
    overrides: [],
    ...extra,
  }) as unknown as PanelObject

const get = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'pa') as PanelObject

function mount(p: PanelObject, ink?: [number, number, number, number], editableSize = false) {
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    manifest: manifestOf(ink, editableSize),
    svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
}

async function setup(extra: Partial<PanelObject> = {}, ink: typeof INK | null = INK, editableSize = false) {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: true })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_edge_crop')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel(extra))
  })
  mount(get(), ink ?? undefined, editableSize)
  useDocumentStore.setState({ past: [], future: [] })
}

const ylabelOut = (fracLeft: number) =>
  ({
    gid: 'ylabel',
    role: 'axis_label',
    label: 'y',
    bbox: [-fracLeft, 0.2, 0.05, 0.6],
    editable: [],
    draggable: true,
  }) as Manifest['elements'][number]

/** 把当前面板的渲染换成带这些元素的 manifest（图幅不变） */
function mountWith(extra: Manifest['elements']) {
  const p = get()
  const base = manifestOf(INK, true)
  useRenderStore.getState().patch(renderKeyOf(p), {
    fileId: p.fileId,
    manifest: { ...base, elements: [...base.elements, ...extra] },
    svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
    rev: 1,
    status: 'ready',
    lastPatches: JSON.stringify(p.overrides),
  })
}

/** 模拟新图回来：同步器按缩放比不变换算面板（`syncPanelNativeSize`），再放上这个变体的精确渲染 */
function settle(size: [number, number], extra: Manifest['elements'] = []) {
  useDocumentStore.setState((s) => ({
    doc: produce(s.doc, (d) => {
      const o = d.objects.find((x) => x.id === 'pa') as PanelObject
      syncPanelNativeSize(o, size[0], size[1])
    }),
  }))
  const p = get()
  const base = manifestOf(INK, true)
  act(() => {
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: p.fileId,
      manifest: { ...base, size_mm: size, elements: [...base.elements, ...extra] },
      svg: '<svg xmlns="http://www.w3.org/2000/svg"/>',
      rev: 2,
      status: 'ready',
      lastPatches: JSON.stringify(p.overrides),
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [p.fileId]: renderKeyOf(p) } }))
  })
}

const down = () =>
  ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  mm: [number, number],
  mods: { altKey?: boolean } = {},
) =>
  window.dispatchEvent(
    new MouseEvent(type, { clientX: mmToWorld(mm[0]), clientY: mmToWorld(mm[1]), bubbles: true, ...mods }),
  )

/** 起手 → 先小挪一下过阈值 → 移到 to → 取读数 → 松手 */
function drag(dir: 'w' | 'e' | 'n' | 's', to: [number, number], mods: { altKey?: boolean } = {}) {
  startPanelHandleDrag(down(), 'pa', dir)
  fire('pointermove', [to[0] / 2, to[1] / 2], mods)
  fire('pointermove', to, mods)
  const status = useInteractionStore.getState().dragStatus
  fire('pointerup', to, mods)
  return status && { text: formatMessage(status.main), tone: status.tone, box: status.box }
}

afterEach(() => {
  useUiStore.getState().setStatus(null)
  useInteractionStore.getState().end()
  useInteractionStore.getState().setFigureFramePending(null)
})

describe('排版里面板的边柄 = 裁剪', () => {
  beforeEach(async () => {
    await setup()
  })

  it('往里拖左边：写 panel.crop，整图在画布上不动（图的缩放比不变），一条历史', () => {
    const readout = drag('w', [20, 0]) // 20mm = 20% 图宽，ink 左缘在 10% → 越过，但先看没按 ⌥ 的结果
    const p = get()
    // 没按 ⌥：停在内容边缘（0.10）
    expect(p.crop?.x).toBeCloseTo(0.1, 12)
    expect(p.crop?.w).toBeCloseTo(0.9, 12)
    expect(p.x).toBeCloseTo(20, 9) // 整图左缘仍在 10：10 + 100 × 0.1
    expect(p.w).toBeCloseTo(90, 9)
    expect(p.y).toBe(20)
    expect(p.h).toBe(75)
    expect(readout).toMatchObject({ text: expect.stringContaining('已到内容边缘'), tone: 'warn' })
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('图边与内容边缘之间 = 裁掉留白（trim），读数带 mm', () => {
    const readout = drag('w', [5, 0])
    const p = get()
    expect(p.crop?.x).toBeCloseTo(0.05, 12)
    expect(readout).toMatchObject({ text: '裁掉留白 · 左 5.0 mm', tone: 'normal' })
    // 状态胶囊贴着被拖面板此刻的框
    expect(readout!.box.x).toBeCloseTo(15, 9)
    expect(readout!.box.w).toBeCloseTo(95, 9)
  })

  it('靠近内容边缘半径内吸上去：紧凑（tight），crop 精确等于内容边缘', () => {
    const readout = drag('w', [9.2, 0])
    expect(get().crop?.x).toBe(0.1)
    expect(readout).toMatchObject({ text: '紧凑 · 左贴合内容', tone: 'normal' })
  })

  it('按住 ⌥ 放行：越过内容边缘，裁掉内容（content）', () => {
    const readout = drag('w', [20, 0], { altKey: true })
    expect(get().crop?.x).toBeCloseTo(0.2, 12)
    expect(readout).toMatchObject({ text: '裁剪 · 左 20.0 mm，将裁掉内容', tone: 'danger' })
  })

  it('右 / 上 / 下三条边各自对应自己的 ink 边缘', () => {
    // 右：ink 右缘 0.9 → 深度 0.1；往左拖 30mm 被挡在 0.1
    let readout = drag('e', [-30, 0])
    expect(get().crop?.w).toBeCloseTo(0.9, 12)
    expect(readout).toMatchObject({ text: expect.stringContaining('已到内容边缘'), tone: 'warn' })
    // 上：ink 上缘 0.2（75mm 的 20% = 15mm）；往下拖 8mm 是留白
    readout = drag('n', [0, 8])
    expect(readout).toMatchObject({ text: '裁掉留白 · 上 8.0 mm' })
    // 下：ink 下缘 0.8 → 深度 0.2；往上拖 100mm 被挡
    readout = drag('s', [0, -100])
    expect(readout).toMatchObject({ tone: 'warn' })
    expect(get().crop?.y).toBeCloseTo(8 / 75, 9)
    expect(get().crop?.h).toBeCloseTo(0.8 - 8 / 75, 9)
  })

  it('往外拖：到「未裁剪」为止，crop 清掉、包围盒回到整图', async () => {
    useDocumentStore.getState().commit(literal('先裁一下'), (d) => {
      const o = d.objects[0] as PanelObject
      o.crop = { x: 0.05, y: 0, w: 0.95, h: 1 }
      o.x = 15
      o.w = 95
    })
    useDocumentStore.setState({ past: [], future: [] })
    const readout = drag('w', [-40, 0])
    const p = get()
    expect(p.crop).toBeUndefined()
    expect(p.x).toBeCloseTo(10, 9)
    expect(p.w).toBeCloseTo(100, 9)
    expect(readout).toMatchObject({ text: '到图边 · 左未裁剪', tone: 'normal' })
  })

  it('没有 ink_bbox（旧后端）：照样能裁，没有紧凑停靠点也没有闸', async () => {
    await setup({}, null)
    const readout = drag('w', [20, 0])
    expect(get().crop?.x).toBeCloseTo(0.2, 12)
    expect(readout).toMatchObject({ text: '裁掉留白 · 左 20.0 mm' })
  })

  it('拖动中读数在，松手后清掉；pointercancel 不留裁剪、不进历史', () => {
    startPanelHandleDrag(down(), 'pa', 'w')
    fire('pointermove', [5, 0])
    fire('pointermove', [10, 0])
    expect(useInteractionStore.getState().kind).toBe('crop')
    expect(useInteractionStore.getState().dragStatus).not.toBeNull()
    expect(get().crop).toBeDefined()
    fire('pointercancel', [10, 0])
    expect(get().crop).toBeUndefined()
    expect(get().x).toBe(10)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useInteractionStore.getState().dragStatus).toBeNull()
    expect(useInteractionStore.getState().kind).toBe('none')
  })

  it('撤销一步回到没裁剪', () => {
    drag('w', [5, 0])
    expect(get().crop).toBeDefined()
    useDocumentStore.getState().undo()
    expect(get().crop).toBeUndefined()
    expect(get().w).toBe(100)
  })
})

describe('不进这条新路径的情形', () => {
  it('旋转过的面板：边柄维持原样（非等比拉伸，不写 crop）', async () => {
    await setup({ rotation: 90 })
    drag('e', [10, 0])
    expect(get().crop).toBeUndefined()
  })

  it('翻转过的面板：同上', async () => {
    await setup({ flipH: true })
    drag('e', [10, 0])
    expect(get().crop).toBeUndefined()
  })

  it('单图编辑（fast_edit）里边柄不裁剪', async () => {
    await setup()
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'pa' })
    drag('w', [20, 0])
    expect(get().crop).toBeUndefined()
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  })

  it('角柄语义不变：不写 crop，等比缩放', async () => {
    await setup()
    drag('w', [0, 0]) // 复位
    startPanelHandleDrag(down(), 'pa', 'se')
    fire('pointermove', [10, 5])
    fire('pointermove', [20, 15])
    fire('pointerup', [20, 15])
    expect(get().crop).toBeUndefined()
    expect(get().w).toBeGreaterThan(100)
  })
})

describe('排版：没裁剪过的图从图边往外拖 = 改图幅（ADR 0161 修订 2026-11-06）', () => {
  const sizeOverride = () => get().overrides.find((o) => o.gid === 'figure' && o.prop === 'size_mm')?.value
  const growing = (dir: 'w' | 'e' | 'n' | 's', to: [number, number]) => {
    startPanelHandleDrag(down(), 'pa', dir)
    fire('pointermove', [to[0] / 2, to[1] / 2])
    fire('pointermove', to)
  }

  beforeEach(async () => {
    await setup({}, INK, true)
  })

  it('往右：松手写 size_mm 一条历史，面板不动不裁、文字比例不变，读数说图幅', () => {
    const readout = drag('e', [20, 0])
    expect(readout).toMatchObject({ text: '改图幅 · 宽 100 → 120 mm', tone: 'normal', box: { x: 10, w: 120 } })
    expect(sizeOverride()).toEqual([120, 75])
    expect(get().crop).toBeUndefined()
    expect(get()).toMatchObject({ x: 10, y: 20, w: 100, h: 75 }) // w 由渲染回来后的同步器按缩放比校正
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(useInteractionStore.getState().figureFramePending).toMatchObject({
      id: 'pa',
      box: { x: 10, y: 20, w: 120, h: 75 },
      sizeMm: [120, 75],
    })
  })

  it('往下只改高；往左 / 上时对边不动', async () => {
    drag('s', [0, 15])
    expect(sizeOverride()).toEqual([100, 90])
    await setup({}, INK, true)
    drag('w', [-20, 0])
    expect(sizeOverride()).toEqual([120, 75])
    expect(get()).toMatchObject({ x: -10, y: 20 })
    await setup({}, INK, true)
    drag('n', [0, -15])
    expect(sizeOverride()).toEqual([100, 90])
    expect(get()).toMatchObject({ x: 10, y: 5 })
  })

  it('拖动中只放预览框、不写文档；虚线框松手后变占位', () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<LayoutFramePreview />))
    expect(container.children).toHaveLength(0)
    act(() => growing('e', [20, 0]))
    expect(useInteractionStore.getState().figureEdgeBox).toMatchObject({ x: 10, w: 120 })
    expect(sizeOverride()).toBeUndefined()
    expect(container.querySelector('[data-figure-preview]')).not.toBeNull()
    act(() => {
      fire('pointerup', [20, 0])
    })
    expect(container.querySelector('[data-figure-preview]')).toBeNull()
    expect(container.querySelector('[data-figure-pending]')).not.toBeNull()
    act(() => root.unmount())
  })

  it('拖回吸附半径内 = 不改；pointercancel 不改、不进历史', () => {
    growing('e', [20, 0])
    fire('pointermove', [1, 0])
    expect(useInteractionStore.getState().figureEdgeBox).toBeNull()
    fire('pointerup', [1, 0])
    expect(sizeOverride()).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(0)

    growing('e', [20, 0])
    fire('pointercancel', [20, 0])
    expect(sizeOverride()).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useInteractionStore.getState().figureFramePending).toBeNull()
  })

  it('同一下拖动先按 ⌥ 往里裁、松开 ⌥ 再往外：裁剪一并丢掉，只留图幅一条历史', () => {
    startPanelHandleDrag(down(), 'pa', 'e')
    fire('pointermove', [-5, 0], { altKey: true })
    expect(get().crop).toBeDefined()
    fire('pointermove', [20, 0])
    fire('pointerup', [20, 0])
    expect(get().crop).toBeUndefined()
    expect(sizeOverride()).toEqual([120, 75])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('往里拖（不按 ⌥）= 缩小图幅，文字 pt 不变；不裁剪（ADR 0161 修订 2026-11-09）', () => {
    const readout = drag('e', [-20, 0])
    expect(readout).toMatchObject({ text: '改图幅 · 宽 100 → 80 mm', box: { x: 10, w: 80 } })
    expect(sizeOverride()).toEqual([80, 75])
    expect(get().crop).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('往里钳到引擎下限（20 mm）', () => {
    drag('e', [-95, 0])
    expect(sizeOverride()).toEqual([20, 75])
  })

  it('按住 ⌥ 往里 = 裁剪，不碰图幅；越过内容边缘不另设闸', () => {
    drag('e', [-5, 0], { altKey: true })
    expect(get().crop?.w).toBeCloseTo(0.95, 12)
    expect(sizeOverride()).toBeUndefined()
    drag('w', [20, 0], { altKey: true })
    expect(get().crop?.x).toBeCloseTo(0.2, 12)
  })

  it('缩小后文字被挤出图幅：新图回来时提示，点了按刚好放得下的图幅出、页面框不动', () => {
    drag('e', [-20, 0])
    expect(sizeOverride()).toEqual([80, 75])
    // 新图回来：y 轴标题探出左边 5 mm
    settle([80, 75], [ylabelOut(5 / 80)])
    const ui = useUiStore.getState()
    expect(ui.status && formatMessage(ui.status)).toBe('文字放不下了，要等比缩小吗？')
    expect(ui.statusAction && formatMessage(ui.statusAction.label)).toBe('缩小到 94%')
    act(() => ui.statusAction!.run())
    expect(useUiStore.getState().status).toBeNull()
    expect(sizeOverride()).toEqual([85, 79.69])
    expect(useDocumentStore.getState().past).toHaveLength(2)
    // 渲染回来同步器按缩放比不变换算后，页面上的框正好是拖到的那个
    settle([85, 79.69])
    expect(get().w).toBeCloseTo(80, 6)
    expect(get().h).toBeCloseTo(75, 6)
    expect(get()).toMatchObject({ x: 10, y: 20 })
    expect(useUiStore.getState().status).toBeNull()
  })

  it('缩小后没有新增探出（原来就压边的不算）：不提示；往外拖从不提示', () => {
    mountWith([ylabelOut(2 / 100)])
    drag('e', [-20, 0])
    settle([80, 75], [ylabelOut(2 / 80)])
    expect(useUiStore.getState().status).toBeNull()
    drag('e', [20, 0])
    settle([100, 75], [ylabelOut(10 / 100)])
    expect(useUiStore.getState().status).toBeNull()
  })

  it('提示挂着时撤销了：点按钮什么都不做', () => {
    drag('e', [-20, 0])
    settle([80, 75], [ylabelOut(5 / 80)])
    const run = useUiStore.getState().statusAction!.run
    act(() => useDocumentStore.getState().undo())
    act(() => run())
    expect(sizeOverride()).toBeUndefined()
  })

  it('裁剪过：退回图边为止，胶囊说先取消裁剪，图幅不动', async () => {
    useDocumentStore.getState().commit(literal('先裁一下'), (d) => {
      const o = d.objects[0] as PanelObject
      o.crop = { x: 0.05, y: 0, w: 0.95, h: 1 }
      o.x = 15
      o.w = 95
    })
    startPanelHandleDrag(down(), 'pa', 'w')
    fire('pointermove', [-20, 0])
    fire('pointermove', [-40, 0])
    const status = useInteractionStore.getState().dragStatus!
    expect(formatMessage(status.main)).toBe('到图边 · 左未裁剪')
    expect(status.detail && formatMessage(status.detail)).toBe('取消裁剪后才能改图幅')
    fire('pointerup', [-40, 0])
    expect(sizeOverride()).toBeUndefined()
  })

  it('图幅不可编辑（没有 size_mm）：停在图边并说明', async () => {
    await setup()
    startPanelHandleDrag(down(), 'pa', 'e')
    fire('pointermove', [10, 0])
    fire('pointermove', [20, 0])
    const status = useInteractionStore.getState().dragStatus!
    expect(formatMessage(status.main)).toBe('到图边 · 右未裁剪')
    expect(status.detail && formatMessage(status.detail)).toBe('这张图现在改不了图幅')
    fire('pointerup', [20, 0])
    expect(sizeOverride()).toBeUndefined()
  })
})
