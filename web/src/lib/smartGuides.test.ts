/**
 * 智能参考线（ADR 0185 M7）：拖动时也吸别的路径的锚点与线段中点、图内的数据点。点候选在 `lib/smartGuides`，
 * 进 `canvas/interactions.candidatesFor` 的 x / y 两组线（参考线的样子不变）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { candidatesFor, startMoveDrag } from '@/canvas/interactions'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ArrowObject, type CanvasObject, type PanelObject, type PathObject, type ShapeObject } from '@/types/document'
import type { Manifest } from './api'
import { DATA_POINT_CAP, dataSnapPoints, pathSnapPoints } from './smartGuides'

const path = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p', type: 'path', x: 10, y: 10, w: 40, h: 20,
  subpaths: [{ closed: false, nodes: [{ p: [0, 1] }, { p: [0.5, 0], in: [0.25, 0], out: [0.75, 0] }, { p: [1, 1] }] }],
  strokePt: 1, color: '#000000', fill: null, ...over,
})
const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 'r', type: 'shape', shape: 'rect', x: 100, y: 100, w: 20, h: 10, strokePt: 1, color: '#000000', fill: null, ...over,
})
const near = (pts: [number, number][], q: [number, number], tol = 1e-6) => pts.some((p) => Math.hypot(p[0] - q[0], p[1] - q[1]) < tol)

describe('路径的锚点与线段中点', () => {
  it('锚点、每段 t = 0.5 的点都在页面上；直线段的中点就是两端的中点', () => {
    const pts = pathSnapPoints(path())
    expect(near(pts, [10, 30])).toBe(true)
    expect(near(pts, [30, 10])).toBe(true)
    expect(near(pts, [50, 30])).toBe(true)
    expect(pts).toHaveLength(5) // 3 个锚点 + 2 段中点
    // 第一段：p0=(10,30) out=(10,30) in=(20,10) p1=(30,10) → B(0.5) = (0.125·10+0.375·10+0.375·20+0.125·30, …)
    expect(near(pts, [0.125 * 10 + 0.375 * 10 + 0.375 * 20 + 0.125 * 30, 0.125 * 30 + 0.375 * 30 + 0.375 * 10 + 0.125 * 10])).toBe(true)
  })
  it('旋转过的路径按页面上的位置给', () => {
    const pts = pathSnapPoints(path({ rotationDeg: 90 }))
    // 绕框中心 (30, 20) 转 90°：(10, 30) → (20, 0)
    expect(near(pts, [20, 0])).toBe(true)
  })
  it('形状按「转为路径」的几何（矩形四角 + 四边中点）；箭头两端 + 中点；文字没有', () => {
    // 形状的描边缺省在内侧：「转为路径」的几何是描边中线，往里缩半个线宽（1 pt 的一半 ≈ 0.18 mm）
    const r = pathSnapPoints(rect())
    expect(r).toHaveLength(8)
    expect(near(r, [100, 100], 0.3) && near(r, [120, 110], 0.3) && near(r, [110, 100], 0.3)).toBe(true)
    const a: ArrowObject = { id: 'a', type: 'arrow', x: 0, y: 0, w: 10, h: 10, start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 }, strokePt: 1, color: '#000', head: 'end' }
    const ap = pathSnapPoints(a)
    expect(near(ap, [0, 0]) && near(ap, [5, 5])).toBe(true)
    expect(pathSnapPoints({ id: 't', type: 'text', x: 0, y: 0, w: 10, h: 5, text: 'x', sizePt: 10, bold: false, color: '#000', align: 'left' } as CanvasObject)).toEqual([])
    expect(pathSnapPoints(path({ hidden: true }))).toEqual([])
  })
})

const panel: PanelObject = {
  id: 'fig', type: 'panel', fileId: 'f.svg', x: 0, y: 0, w: 100, h: 50, nativeW: 100, nativeH: 50, overrides: [],
} as unknown as PanelObject
const manifest = (els: Partial<Manifest['elements'][number]>[]): Manifest =>
  ({ elements: els.map((e, i) => ({ gid: `g${i}`, role: 'line', label: '', bbox: [0, 0, 1, 1], editable: [], draggable: false, ...e })) }) as unknown as Manifest

describe('图内数据点', () => {
  it('曲线的折线顶点、散点每颗 marker 的中心；裁剪窗外的不要；柱子这类没有点的不要', () => {
    const m = manifest([
      { role: 'line', geometry: { kind: 'polyline', paths: [{ points: [[0.1, 0.2], [0.5, 0.5], [1.4, 0.5]], closed: false }], fill: false, stroke: true } },
      { role: 'scatter', geometry: { kind: 'multi_path', paths: [{ points: [[0.6, 0.6], [0.62, 0.6], [0.62, 0.62], [0.6, 0.62]], closed: true }], fill: true, stroke: false } },
      { role: 'bar', geometry: { kind: 'path', paths: [{ points: [[0.3, 0.9], [0.4, 0.9]], closed: true }], fill: true, stroke: false } },
    ])
    const pts = dataSnapPoints(panel, m)
    expect(near(pts, [10, 10])).toBe(true)
    expect(near(pts, [50, 25])).toBe(true)
    expect(near(pts, [61, 30.5])).toBe(true)
    expect(near(pts, [140, 25])).toBe(false)
    expect(pts).toHaveLength(3)
  })
  it('一张图最多给这么多点', () => {
    const many = Array.from({ length: DATA_POINT_CAP + 50 }, (_, i) => [((i % 100) + 0.5) / 100, 0.5] as [number, number])
    const pts = dataSnapPoints(panel, manifest([{ role: 'line', geometry: { kind: 'polyline', paths: [{ points: many, closed: false }], fill: false, stroke: true } }]))
    expect(pts).toHaveLength(DATA_POINT_CAP)
  })
})

describe('拖动时吸到别的路径的锚点', () => {
  beforeEach(async () => {
    localStorage.clear()
    useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
    useUiStore.setState({ tool: 'select', snapEnabled: true, snapToObjects: true, snapToGrid: false, snapToGuides: false, elementPanelId: null })
    useSelectionStore.getState().clear()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_smart_' + Math.random())
    useDocumentStore.getState().commit(literal('放'), (d) => {
      d.objects.push(path(), rect())
    })
  })
  afterEach(() => useInteractionStore.getState().end())

  // 路径的框是 x 10–50（中线 30）、y 10–30（中线 20）：第一段曲线 t = 0.5 处 x = 16.25，不在框的边 / 中线上，只有智能参考线给
  it('候选线里有别的路径的锚点 / 中点的 x、y；被拖的对象自己的不算', () => {
    const c = candidatesFor(new Set(['r']))!
    expect(c.xs.some((x) => Math.abs(x - 16.25) < 1e-9)).toBe(true)
    const own = candidatesFor(new Set(['p']))!
    expect(own.xs.some((x) => Math.abs(x - 16.25) < 1e-9)).toBe(false)
  })

  it('把矩形拖到左边缘靠近路径那段曲线中点 x = 16.25 的地方：吸上去，参考线亮起', () => {
    useSelectionStore.getState().set(['r'])
    const down = { clientX: 0, clientY: 0, button: 0, stopPropagation() {} } as unknown as React.PointerEvent
    startMoveDrag(down, 'r')
    // 左边缘 100 → 16.65（离中点 x = 16.25 不到吸附容差）
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: mmToWorld(-83.35), clientY: mmToWorld(5), bubbles: true }))
    const snapX = useInteractionStore.getState().snapXs
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: mmToWorld(-83.35), clientY: mmToWorld(5), bubbles: true }))
    const r = useDocumentStore.getState().doc.objects.find((o) => o.id === 'r')!
    expect(r.x).toBeCloseTo(16.25, 6)
    expect(snapX.some((x) => Math.abs(x - 16.25) < 1e-9)).toBe(true)
  })
})
