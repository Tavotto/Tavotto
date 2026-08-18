/**
 * 图内独立箭头（FancyArrowPatch）与图内元素的画布交互。
 *
 * 背景（修之前）：图内箭头沿用「bbox 矩形」三件套——命中吃整个空白包围盒、
 * 选中画一个与线对不上的矩形外框、拖动不认 shift。画布箭头早已是沿线命中 +
 * 沿线描示 + shift 锁角（见 hitTest.test / shiftConstraints.test），图内箭头
 * 这里对齐到同一套语义：
 *
 * - pickElement：按线本身命中（容差 1.5mm），bbox 空白区让给底下的元素
 * - 框选：线段与框选带相交才算圈中
 * - OverlaySvg：选中 / hover 是沿线 <line>，没有矩形外框
 * - startArrowDrag：拖单端点 shift 锁 15°（相对固定端）、整体拖 shift 锁
 *   水平 / 垂直 / 45°；分数坐标 x/y 分别除以图宽图高，角度在内容像素系里算
 * - startElementDrag：图内文字类拖动同样 shift 锁向
 * - 画布对象拖动可吸附图内元素（文字 / 子图）的中心线（elementSnapCandidates）
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import { elementSnapCandidates, segIntersectsRect } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { pickElement, startArrowDrag, startElementDrag, startMoveDrag } from './interactions'

/* ------------------------------ 测试用数据 ------------------------------- */

const figureEl: ManifestElement = {
  gid: 'figure',
  role: 'figure',
  label: '整图',
  bbox: [0, 0, 1, 1],
  editable: [],
  draggable: false,
}

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图',
  bbox: [0.1, 0.1, 0.8, 0.8],
  editable: [],
  draggable: false,
  resizable: true,
}

const textEl: ManifestElement = {
  gid: 'text_1',
  role: 'text',
  label: '文字',
  bbox: [0.2, 0.4, 0.2, 0.1],
  editable: [],
  draggable: true,
  anchor: [0.3, 0.45],
  drag_prop: 'pos_frac',
}

/** 斜箭头：bbox 是 0.4×0.3 的一大块，但真实的线只是那条对角线 */
const arrowEl: ManifestElement = {
  gid: 'arrow_0',
  role: 'arrow',
  label: '箭头',
  bbox: [0.2, 0.16, 0.4, 0.24],
  editable: [],
  draggable: true,
  arrow_endpoints: [
    [0.2, 0.4],
    [0.6, 0.16],
  ],
}

/** 水平箭头：shift 锁角断言用（角度算得出整数结果） */
const flatArrowEl: ManifestElement = {
  gid: 'arrow_1',
  role: 'arrow',
  label: '水平箭头',
  bbox: [0.2, 0.5, 0.4, 0],
  editable: [],
  draggable: true,
  arrow_endpoints: [
    [0.2, 0.5],
    [0.6, 0.5],
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [figureEl, axesEl, textEl, arrowEl, flatArrowEl],
}

const panel = (): PanelObject => ({
  id: 'p1',
  type: 'panel',
  x: 10,
  y: 20,
  w: 100,
  h: 80,
  fileId: 'f1',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 80,
  script: 'fig.py',
  overrides: [],
})

/** PanelView 传给交互层的 layout：内容的世界像素尺寸 */
const layout = { width: mmToWorld(100), height: mmToWorld(80) }

/* ------------------------------ 指针事件桩 ------------------------------- */

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent

const fire = (
  type: 'pointermove' | 'pointerup',
  clientX: number,
  clientY: number,
  init: MouseEventInit = {},
) => window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true, ...init }))

const px = (mm: number) => mmToWorld(mm)

const overrideOf = (gid: string, prop: string) => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') return undefined
  return p.overrides.find((o) => o.gid === gid && o.prop === prop)?.value
}

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({
    tool: 'select',
    snapEnabled: false,
    elementPanelId: null,
    cropTargetId: null,
    selectedGids: [],
  })
  useSelectionStore.getState().clear()
  // setOverride 松手会触发引擎渲染；测试里掐断网络那一步
  useRenderStore.setState({ render: async () => {} })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_el_arrow')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  // 渲染态按「文件 + 变体」分键：种进这个面板自己的那份
  useRenderStore.getState().patch(renderKeyOf(panel()), {
    fileId: 'f1',
    manifest,
    status: 'ready',
  })
})

afterEach(() => {
  useInteractionStore.getState().end()
})

/* ------------------------------ 命中测试 --------------------------------- */

describe('pickElement：图内箭头按线本身命中', () => {
  it('点在线上（bbox 与子图重叠区）命中箭头', () => {
    // 线段中点 (0.4, 0.28)：在箭头 bbox 里、也在子图 bbox 里
    expect(pickElement(manifest, 0.4, 0.28)?.gid).toBe('arrow_0')
  })

  it('点在 bbox 空白区（离线很远）不再命中箭头，落到底下的元素', () => {
    // (0.28, 0.42)：在箭头 bbox 的左下空白角，同时在文字 bbox 里
    expect(pickElement(manifest, 0.28, 0.42)?.gid).toBe('text_1')
  })

  it('bbox 空白区下面没有别的元素时命中容器，而不是箭头', () => {
    // (0.58, 0.38)：箭头 bbox 右下空白角，文字之外 → 子图
    expect(pickElement(manifest, 0.58, 0.38)?.gid).toBe('axes_0')
  })
})

describe('segIntersectsRect：框选按线段相交', () => {
  const r = { x: 0.35, y: 0.2, w: 0.1, h: 0.2 }
  it('线穿过框（两端都在框外）算圈中', () => {
    expect(segIntersectsRect([0.2, 0.4], [0.6, 0.16], r)).toBe(true)
  })
  it('框只碰到 bbox 空白角、没碰到线，不算圈中', () => {
    expect(segIntersectsRect([0.2, 0.4], [0.6, 0.16], { x: 0.5, y: 0.35, w: 0.08, h: 0.04 })).toBe(
      false,
    )
  })
  it('端点落在框内算圈中', () => {
    expect(segIntersectsRect([0.2, 0.4], [0.6, 0.16], { x: 0.15, y: 0.35, w: 0.1, h: 0.1 })).toBe(
      true,
    )
  })
})

/* ------------------------------ 覆盖层描示 -------------------------------- */

describe('OverlaySvg：图内箭头选中沿线描示，无矩形外框', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('选中箭头：出两个端点圆 + 一条沿线描示，没有带底色的选中矩形', () => {
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['arrow_0'] })
    act(() => root.render(<OverlaySvg />))

    expect(container.querySelectorAll('circle[data-arrow-endpoint]').length).toBe(2)
    // 选中矩形的指纹是 fill-opacity=0.06 的 rect；箭头不许再有
    expect(container.querySelectorAll('rect[fill-opacity]').length).toBe(0)
    const line = [...container.querySelectorAll('line')].find(
      (l) => l.getAttribute('stroke-width') === '1.5',
    )
    expect(line).toBeTruthy()
    // 描示线端点 = 面板内容矩形上的真实端点（面板 x=10,y=20，内容 100×80mm）
    expect(Number(line!.getAttribute('x1'))).toBeCloseTo(px(10 + 0.2 * 100), 4)
    expect(Number(line!.getAttribute('y1'))).toBeCloseTo(px(20 + 0.4 * 80), 4)
  })

  it('选中文字元素仍是矩形选中框（回归对照）', () => {
    useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['text_1'] })
    act(() => root.render(<OverlaySvg />))
    expect(container.querySelectorAll('rect[fill-opacity]').length).toBe(1)
  })
})

/* ------------------------------ shift 锁定 -------------------------------- */

describe('startArrowDrag：shift 锁角 / 锁向', () => {
  it('拖单端点 + shift：近水平的拖动锁成纯水平（15° 档）', () => {
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    startArrowDrag(down(0, 0), p, flatArrowEl, layout, 'end')
    // 位移换算成分数：dfx=0.2、dfy=0.03 → 角度 ≈ 2.3°，shift 吸到 0°
    fire('pointermove', px(20), px(2.4), { shiftKey: true })
    fire('pointerup', px(20), px(2.4), { shiftKey: true })

    const v = overrideOf('arrow_1', 'endpoints_frac') as number[]
    expect(v).toBeTruthy()
    expect(v[0]).toBeCloseTo(0.2, 6)
    expect(v[1]).toBe(0.5) // 固定端原样
    expect(v[3]).toBe(0.5) // 锁水平：终点 y 逐位回到起点 y
    expect(v[2]).toBeGreaterThan(0.79)
  })

  it('拖单端点不按 shift：位移原样落下（回归对照）', () => {
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    startArrowDrag(down(0, 0), p, flatArrowEl, layout, 'end')
    fire('pointermove', px(20), px(2.4))
    fire('pointerup', px(20), px(2.4))

    const v = overrideOf('arrow_1', 'endpoints_frac') as number[]
    expect(v[2]).toBeCloseTo(0.8, 4)
    expect(v[3]).toBeCloseTo(0.53, 4)
  })

  it('整体拖动 + shift：近水平锁成纯水平（垂直分量逐位归零）', () => {
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    startArrowDrag(down(0, 0), p, flatArrowEl, layout, 'both')
    fire('pointermove', px(30), px(2), { shiftKey: true })
    fire('pointerup', px(30), px(2), { shiftKey: true })

    const v = overrideOf('arrow_1', 'endpoints_frac') as number[]
    expect(v[0]).toBeCloseTo(0.5, 6)
    expect(v[1]).toBe(0.5)
    expect(v[2]).toBeCloseTo(0.9, 6)
    expect(v[3]).toBe(0.5)
  })

  it('拖端点中的虚线预览与松手落点一致（shift 状态取自 onMove）', () => {
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    startArrowDrag(down(0, 0), p, flatArrowEl, layout, 'end')
    fire('pointermove', px(20), px(2.4), { shiftKey: true })
    const preview = useInteractionStore.getState().arrowPreview
    expect(preview?.b[1]).toBe(0.5)
    // 松手事件没带 shiftKey 也不改结果：写的是预览里那对端点
    fire('pointerup', px(20), px(2.4))
    const v = overrideOf('arrow_1', 'endpoints_frac') as number[]
    expect(v[3]).toBe(0.5)
  })
})

describe('startElementDrag：图内文字拖动 shift 锁向', () => {
  it('近水平拖动 + shift：锚点只动 x，y 逐位不变', () => {
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    startElementDrag(down(0, 0), p, textEl, layout)
    fire('pointermove', px(20), px(1.6), { shiftKey: true })
    fire('pointerup', px(20), px(1.6), { shiftKey: true })

    const v = overrideOf('text_1', 'pos_frac') as number[]
    expect(v[0]).toBeCloseTo(0.5, 6)
    expect(v[1]).toBe(0.45)
  })
})

/* --------------------------- 图内元素中心线吸附 ---------------------------- */

describe('elementSnapCandidates：图内元素中心线', () => {
  it('文字与子图出中心线（页面 mm），独立箭头不出', () => {
    const { xs, ys } = elementSnapCandidates(panel(), manifest)
    // 文字中心 fx=0.3 → 10 + 30 = 40；子图中心 fx=0.5 → 60
    expect(xs).toContain(40)
    expect(xs).toContain(60)
    // 文字中心 fy=0.45 → 20 + 36 = 56
    expect(ys).toContain(56)
    // 箭头（arrow_endpoints）不出中心线：两条箭头的 bbox 中心都不在候选里
    expect(xs.filter((x) => Math.abs(x - 50) < 1e-9).length).toBe(0)
  })

  it('元素被 override 挪过但渲染未回来时，中心跟着新锚点走', () => {
    const p = panel()
    p.overrides = [{ gid: 'text_1', prop: 'pos_frac', value: [0.6, 0.55] }]
    const { xs, ys } = elementSnapCandidates(p, manifest)
    expect(xs).toContain(10 + (0.3 + 0.3) * 100) // bbox 中心随锚点平移 +0.3
    expect(ys).toContain(20 + (0.45 + 0.1) * 80)
  })

  it('拖画布对象时真的吸上：矩形中心吸到图内文字中心线', () => {
    useUiStore.setState({
      snapEnabled: true,
      snapToObjects: true,
      snapToGuides: false,
      snapToGrid: false,
    })
    const r: ShapeObject = {
      id: 'r1',
      type: 'shape',
      shape: 'rect',
      x: 130,
      y: 110,
      w: 10,
      h: 10,
      strokePt: 1,
      color: '#111111',
      fill: null,
    }
    useDocumentStore.getState().commit(literal('加'), (d) => {
      d.objects.push(r)
    })
    useSelectionStore.getState().set(['r1'])
    startMoveDrag(down(0, 0), 'r1')
    // 目标：中心落到 x=40（文字中心线）差 0.4mm 处 → 吸附补齐
    fire('pointermove', px(35.4 - 130), px(-59.5))
    fire('pointerup', px(35.4 - 130), px(-59.5))

    const o = useDocumentStore.getState().doc.objects.find((x) => x.id === 'r1')!
    expect(o.x + 5).toBeCloseTo(40, 6)
  })
})
