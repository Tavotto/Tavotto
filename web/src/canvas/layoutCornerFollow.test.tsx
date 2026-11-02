/**
 * 多图排版里面板角缩放的「连同标注」（ADR 0161）：与这张面板相交的标注按同一固定点、同一比例缩放
 * 位置 / 尺寸 / 所有页面 pt 量，同一个事务（一条历史）；不相交 / 锁定 / 隐藏的不动；开关关掉、非等比、
 * 单图编辑里都不跟。
 *
 * 版面：pa 在 (10,20) 100×75，拖东南角（固定点 = 左上角 (10,20)）。
 */
import { literal } from '@/i18n'
import { beforeEach, afterEach, describe, expect, it } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useProfileStore } from '@/store/profileStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { UI_PREFS_KEY, useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import {
  emptyProject,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { startResizeDrag } from './interactions'

const panel = (): PanelObject =>
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
  }) as unknown as PanelObject

const text = (extra: Partial<TextObject> = {}): TextObject =>
  ({
    id: 't1',
    type: 'text',
    text: 'a',
    sizePt: 10,
    bold: false,
    color: '#000',
    align: 'left',
    x: 30,
    y: 30,
    w: 40,
    h: 8,
    borderPt: 0.5,
    padding: 1,
    ...extra,
  }) as TextObject

const arrow = (extra: Partial<ArrowObject> = {}): ArrowObject =>
  ({
    id: 'a1',
    type: 'arrow',
    x: 50,
    y: 50,
    w: 20,
    h: 10,
    start: { rx: 0, ry: 0 },
    end: { rx: 1, ry: 1 },
    strokePt: 1.2,
    color: '#000',
    head: 'end',
    ...extra,
  }) as ArrowObject

const shape = (extra: Partial<ShapeObject> = {}): ShapeObject =>
  ({
    id: 's1',
    type: 'shape',
    shape: 'rect',
    x: 100,
    y: 85,
    w: 30,
    h: 30,
    strokePt: 2,
    cornerRadius: 3,
    color: '#000',
    fill: null,
    ...extra,
  }) as ShapeObject

const get = <T extends CanvasObject>(id: string) =>
  useDocumentStore.getState().doc.objects.find((o) => o.id === id) as T

async function setup(extras: CanvasObject[] = [text(), arrow(), shape()]) {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  // 关吸附：这里只看"跟随"，不让缩放吸附把比例拽走
  useUiStore.setState({ tool: 'select', snapEnabled: false, scaleWithAnnotations: true })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_corner_follow')
  useDocumentStore.getState().commit(literal('摆放'), (d) => {
    d.objects.push(panel(), ...extras)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

const down = () =>
  ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (
  type: 'pointermove' | 'pointerup',
  mm: [number, number],
  mods: { altKey?: boolean } = {},
) =>
  window.dispatchEvent(
    new MouseEvent(type, { clientX: mmToWorld(mm[0]), clientY: mmToWorld(mm[1]), bubbles: true, ...mods }),
  )

/** 拖东南角，返回拖动中最后一帧的读数 */
function dragCorner(to: [number, number], mods: { altKey?: boolean } = {}) {
  startResizeDrag(down(), 'pa', 'se')
  fire('pointermove', [to[0] / 2, to[1] / 2], mods)
  fire('pointermove', to, mods)
  const readout = useInteractionStore.getState().layoutReadout
  fire('pointerup', to, mods)
  return readout
}

beforeEach(async () => {
  await setup()
})
afterEach(() => {
  useInteractionStore.getState().end()
})

describe('连同标注', () => {
  it('位置、尺寸、pt 量按同一个固定点、同一个比例缩放，一条历史', () => {
    dragCorner([-50, -37.5])
    const p = get<PanelObject>('pa')
    const s = p.w / 100
    expect(s).toBeGreaterThan(0.3)
    expect(s).toBeLessThan(0.7)
    expect(p.h / 75).toBeCloseTo(s, 9)

    // 固定点 = 左上角 (10, 20)；位置按框中心位似
    const t = get<TextObject>('t1')
    expect(t.w).toBeCloseTo(40 * s, 9)
    expect(t.h).toBeCloseTo(8 * s, 9)
    expect(t.x + t.w / 2).toBeCloseTo(10 + (50 - 10) * s, 9)
    expect(t.y + t.h / 2).toBeCloseTo(20 + (34 - 20) * s, 9)
    expect(t.sizePt).toBeCloseTo(10 * s, 9)
    expect(t.borderPt).toBeCloseTo(0.5 * s, 9)
    expect(t.padding).toBeCloseTo(1 * s, 9)

    const a = get<ArrowObject>('a1')
    expect(a.strokePt).toBeCloseTo(1.2 * s, 9)
    expect(a.w).toBeCloseTo(20 * s, 9)
    expect(a.start).toEqual({ rx: 0, ry: 0 }) // 端点是相对框的比例，不变
    expect(a.end).toEqual({ rx: 1, ry: 1 })

    // 与面板只相交一部分的形状也跟着走
    const sh = get<ShapeObject>('s1')
    expect(sh.strokePt).toBeCloseTo(2 * s, 9)
    expect(sh.cornerRadius).toBeCloseTo(3 * s, 9)
    expect(sh.w).toBeCloseTo(30 * s, 9)

    // 面板与标注在同一个事务里：整手势一条历史，撤销一步全部回来
    expect(useDocumentStore.getState().past).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(get<PanelObject>('pa').w).toBe(100)
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30, w: 40, sizePt: 10, borderPt: 0.5, padding: 1 })
    expect(get<ArrowObject>('a1').strokePt).toBe(1.2)
  })

  it('不相交 / 锁定 / 隐藏的标注不动', async () => {
    await setup([
      text({ id: 'far', x: 300, y: 300 }),
      text({ id: 'locked', locked: true }),
      text({ id: 'hidden', hidden: true }),
      text({ id: 'in' }),
    ])
    dragCorner([-50, -37.5])
    for (const id of ['far', 'locked', 'hidden']) {
      const o = get<TextObject>(id)
      expect(o.sizePt).toBe(10)
      expect(o.w).toBe(40)
    }
    expect(get<TextObject>('far').x).toBe(300)
    expect(get<TextObject>('in').sizePt).toBeLessThan(10)
  })

  it('放大也一样（比例 > 1）', () => {
    dragCorner([50, 37.5])
    const s = get<PanelObject>('pa').w / 100
    expect(s).toBeGreaterThan(1.3)
    expect(get<TextObject>('t1').sizePt).toBeCloseTo(10 * s, 9)
  })

  it('开关关掉：标注不动', () => {
    useUiStore.setState({ scaleWithAnnotations: false })
    const readout = dragCorner([-50, -37.5])
    expect(get<PanelObject>('pa').w).toBeLessThan(100)
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30, w: 40, sizePt: 10 })
    expect(readout).toMatchObject({ kind: 'scale', follow: { on: false } })
  })

  it('⌥ 反转比例锁定 = 非等比：没有"一个比例"，标注留在原处', () => {
    const readout = dragCorner([-50, -10], { altKey: true })
    const p = get<PanelObject>('pa')
    expect(p.w / 100).not.toBeCloseTo(p.h / 75, 3)
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, y: 30, w: 40, sizePt: 10 })
    expect(readout).toMatchObject({ kind: 'scale', follow: { on: false } })
  })

  it('单图编辑（fast_edit）里不跟，也没有读数', () => {
    useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'pa' })
    const readout = dragCorner([-50, -37.5])
    expect(get<TextObject>('t1')).toMatchObject({ x: 30, sizePt: 10 })
    expect(readout).toBeNull()
    useWorkspaceStore.setState({ mode: 'layout', activePanelId: null })
  })

  it('开关偏好写进 uiStore 并落本机（默认开）', () => {
    expect(useUiStore.getState().scaleWithAnnotations).toBe(true)
    useUiStore.getState().setCanvasPref({ scaleWithAnnotations: false })
    const saved = JSON.parse(localStorage.getItem(UI_PREFS_KEY) ?? '{}')
    expect(saved.scaleWithAnnotations).toBe(false)
  })
})

describe('缩放读数', () => {
  it('缩放百分比与图内文字等效 pt（panelScale / effectivePt 同一份算法）', () => {
    const readout = dragCorner([-50, -37.5])
    const p = get<PanelObject>('pa')
    const s = p.w / 100
    expect(readout).toMatchObject({
      kind: 'scale',
      fromPct: 100,
      toPct: Math.round(s * 100),
      fromPt: 9,
      follow: { on: true, count: 3 },
    })
    expect((readout as { toPt: number }).toPt).toBeCloseTo(9 * s, 9)
  })

  it('低于规范下限 → low；规范库没载入读不到下限 → 不判', () => {
    useProfileStore.setState({ loaded: false })
    expect(dragCorner([-50, -37.5])).toMatchObject({ low: false })
    useDocumentStore.getState().undo()
    useProfileStore.setState({ loaded: true })
    const readout = dragCorner([-50, -37.5]) as { low: boolean; toPt: number }
    // 默认规范的字号下限（8pt 左右）：缩到约 50% 后图内文字 ≈ 4.5pt，低于它
    expect(readout.toPt).toBeLessThan(8)
    expect(readout.low).toBe(true)
  })

  it('放大不低于下限', () => {
    useProfileStore.setState({ loaded: true })
    expect(dragCorner([50, 37.5])).toMatchObject({ low: false })
  })

  it('位图源不报 pt（按 dpi 评质量）', async () => {
    await setup()
    useDocumentStore.getState().commit(literal('改成位图'), (d) => {
      ;(d.objects[0] as PanelObject).fileKind = 'raster'
    })
    expect(dragCorner([-50, -37.5])).toMatchObject({ kind: 'scale', fromPt: null, toPt: null })
  })

  it('松手后读数清掉', () => {
    dragCorner([-50, -37.5])
    expect(useInteractionStore.getState().layoutReadout).toBeNull()
  })
})
